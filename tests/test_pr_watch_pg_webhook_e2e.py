"""PostgreSQL 16 -> real signed HTTP -> Temporal -> user-visible API acceptance.

Uses a real disposable PG database and real Temporal SDK test server.
GitHub observations are fixtures; no public listener or GitHub credentials.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("temporalio")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from temporalio import activity  # noqa: E402
from temporalio.api.enums.v1 import EventType  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from pr_watch_control_bridge import workflow_id  # noqa: E402
from pr_watch_harness import PullSnapshot  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402
from pr_watch_pg_webhook import PostgresWebhookIngress, PostgresWebhookSchema  # noqa: E402
from pr_watch_service import LocalTokens, PilotHTTPServer, PilotService  # noqa: E402
from pr_watch_temporal import PRWatchWorkflow  # noqa: E402
from pr_watch_webhook import WebhookServer  # noqa: E402
from test_pr_watch_service import request  # noqa: E402
from test_pr_watch_webhook import post  # noqa: E402

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")
REPO = "avaargsh/agent-control-plane"
PR = 169
SECRET = b"p" * 40


@contextmanager
def serve_http(cls, service):
    server = cls(("127.0.0.1", 0), service)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


@pytest.mark.timeout(75)
def test_postgres_real_http_check_wake_to_temporal_and_scoped_events():
    if not DSN:
        pytest.skip("requires disposable PR_WATCH_PG_TEST_DSN")

    async def scenario():
        store = PostgresWatchStore(DSN)
        store.install_v2()
        PostgresWebhookSchema(DSN).install()
        principal = "wake_" + uuid4().hex
        goal_id = "goal_" + uuid4().hex
        reads = []
        first_observed = asyncio.Event()
        second_observed = asyncio.Event()

        class FixtureReader:
            def __init__(self, snapshot):
                self.snapshot = snapshot

            def fetch(self, repo, number):
                assert (repo, number) == (REPO, PR)
                return self.snapshot

        @activity.defn(name="read_and_persist_pr")
        async def observe(spec: dict) -> dict:
            reads.append(spec["goal_id"])
            snapshot = PullSnapshot(
                "a" * 40, "OPEN",
                "FAILURE" if len(reads) == 1 else "SUCCESS",
            )
            result = store.tick(
                user_id=spec["user_id"], goal_id=spec["goal_id"],
                reader=FixtureReader(snapshot), now=datetime.now(timezone.utc),
            )
            if len(reads) == 1:
                first_observed.set()
            else:
                second_observed.set()
            return result

        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client, task_queue="pg-webhook-http-temporal-e2e",
                workflows=[PRWatchWorkflow], activities=[observe],
            ):
                tokens = {principal: "a" * 40, "other": "b" * 40}
                api = PilotService(
                    None, LocalTokens(tokens),
                    storage_backend="postgres", pg_dsn=DSN,
                )
                with serve_http(PilotHTTPServer, api) as api_http:
                    status, response, _ = await asyncio.to_thread(
                        request, api_http, "POST", "/api/goals",
                        who=None, body={
                            "goal_id": goal_id, "repo": REPO, "number": PR,
                        },
                        headers={"Authorization": "Bearer " + tokens[principal]},
                    )
                    assert status == 200, response
                    assert json.loads(response)["goal"]["state"] == "RUNNING"
                    assert store.goal(user_id=principal, goal_id=goal_id)["repo"] == REPO

                    handle = await env.client.start_workflow(
                        PRWatchWorkflow.run,
                        {"user_id": principal, "goal_id": goal_id,
                         "interval_seconds": 86400},
                        id=workflow_id(user_id=principal, goal_id=goal_id),
                        task_queue="pg-webhook-http-temporal-e2e",
                    )
                    try:
                        await asyncio.wait_for(first_observed.wait(), timeout=15)
                        # A Timer must be durably scheduled before the
                        # webhook arrives, otherwise an ordinary first
                        # Activity completion could mimic wake behavior.
                        async def wait_for_timer():
                            for _ in range(120):
                                history = await handle.fetch_history()
                                if any(
                                    e.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                                    for e in history.events
                                ):
                                    return
                                await asyncio.sleep(0.05)
                            raise AssertionError("Temporal timer not persisted")

                        await asyncio.wait_for(wait_for_timer(), timeout=15)
                        loop = asyncio.get_running_loop()

                        def send_signal(signal_id):
                            result = asyncio.run_coroutine_threadsafe(
                                handle.signal("wake", signal_id), loop,
                            )
                            result.result(timeout=10)

                        ingress = PostgresWebhookIngress(
                            store=PostgresWatchStore(DSN), user_id=principal,
                            goal_id=goal_id, secret=SECRET, signal=send_signal,
                        )
                        with serve_http(WebhookServer, ingress) as webhook_http:
                            # The first provider observation is a durable
                            # CI failure; bogus signatures must not wake it.
                            check = json.dumps({
                                "action": "completed",
                                "repository": {"full_name": REPO},
                                "check_run": {
                                    "pull_requests": [{"number": PR}],
                                    "conclusion": "success",
                                    "name": "untrusted-ci-name",
                                },
                            }).encode()
                            event = {
                                "signature": "sha256=" + hmac.new(
                                    SECRET, check, hashlib.sha256,
                                ).hexdigest(),
                                "event": "check_run",
                                "delivery": str(uuid4()), "body": check,
                            }
                            bad = {**event, "signature": "sha256=" + "0" * 64}
                            assert (await asyncio.to_thread(
                                post, webhook_http, bad,
                            ))[0] == 403
                            first_code, first = await asyncio.to_thread(
                                post, webhook_http, event,
                            )
                            second_code, duplicate = await asyncio.to_thread(
                                post, webhook_http, event,
                            )
                            assert first_code == 202 and first["signal_state"] == "ACKED"
                            assert second_code == 200 and duplicate == {
                                "status": "duplicate", "signal_state": "ACKED",
                            }
                            await asyncio.wait_for(second_observed.wait(), timeout=20)
                            current = store.goal(
                                user_id=principal, goal_id=goal_id,
                            )
                            assert current["phase"] == "CI_CLEARED"
                            with store._connection() as conn:
                                saved = conn.execute(
                                    """SELECT state FROM pr_watch_pg_webhook_wakes
                                       WHERE user_id=%s AND goal_id=%s""",
                                    (principal, goal_id),
                                ).fetchall()
                            assert len(saved) == 1 and saved[0]["state"] == "ACKED"
                            own_status, own_response, _ = await asyncio.to_thread(
                                request, api_http, "GET", "/api/events?after=0",
                                headers={"Authorization": "Bearer " + tokens[principal]},
                            )
                            assert own_status == 200
                            own_events = json.loads(own_response)["events"]
                            assert any(e["kind"] == "ACTION_REQUIRED" for e in own_events)
                            assert any(e["kind"] == "TASK_SUCCEEDED" for e in own_events)
                            other_status, other_response, _ = await asyncio.to_thread(
                                request, api_http, "GET", "/api/events?after=0",
                                headers={"Authorization": "Bearer " + tokens["other"]},
                            )
                            assert other_status == 200
                            assert all(
                                e.get("goal_id") != goal_id
                                for e in json.loads(other_response)["events"]
                            )
                            history = await handle.fetch_history()
                            assert sum(
                                x.event_type ==
                                EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
                                for x in history.events
                            ) == 1
                    finally:
                        # Test hygiene: no unattended long-lived Workflow
                        # continues after assertion failures.
                        await handle.terminate()
    asyncio.run(scenario())
