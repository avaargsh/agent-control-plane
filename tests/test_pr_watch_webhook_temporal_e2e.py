"""Real Temporal test-server end-to-end for the signed webhook wake hint.

Tests a real Workflow + Signal + Activity + SQLite; GitHub remains a fixture.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("temporalio")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from pr_watch_control_bridge import workflow_id
from pr_watch_harness import GoalStore, PullSnapshot
from pr_watch_temporal import PRWatchWorkflow
from pr_watch_webhook import GitHubWebhookIngress


def test_signed_webhook_wakes_existing_workflow_and_observes_real_state(tmp_path):
    async def scenario():
        store = GoalStore(tmp_path / "watch.db")
        store.register(
            user_id="alice", goal_id="webhook-e2e",
            repo="avaargsh/agent-control-plane", number=169,
        )
        seen = []
        observed_first = asyncio.Event()

        class Reader:
            def __init__(self, snapshot):
                self.snapshot = snapshot

            def fetch(self, repo, number):
                assert repo == "avaargsh/agent-control-plane" and number == 169
                return self.snapshot

        @activity.defn(name="read_and_persist_pr")
        async def observe(spec: dict) -> dict:
            seen.append(spec["goal_id"])
            if len(seen) > 2:
                raise AssertionError("unexpected extra provider reads")
            snapshot = (
                PullSnapshot("a" * 40, "OPEN", "PENDING")
                if len(seen) == 1 else
                PullSnapshot("a" * 40, "MERGED", "UNKNOWN")
            )
            result = store.tick(
                user_id="alice", goal_id=spec["goal_id"],
                reader=Reader(snapshot), now=datetime.now(timezone.utc),
            )
            if len(seen) == 1:
                observed_first.set()
            return result

        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client, task_queue="webhook-wake-real-test",
                workflows=[PRWatchWorkflow], activities=[observe],
            ):
                key = workflow_id(user_id="alice", goal_id="webhook-e2e")
                handle = await env.client.start_workflow(
                    PRWatchWorkflow.run,
                    {"user_id": "alice", "goal_id": "webhook-e2e",
                     "interval_seconds": 86400},
                    id=key, task_queue="webhook-wake-real-test",
                )
                await asyncio.wait_for(observed_first.wait(), timeout=15)
                # Wait for a committed Timer before sending the signed event.
                async def timer_persisted():
                    for _ in range(120):
                        history = await handle.fetch_history()
                        if any(
                            e.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                            for e in history.events
                        ):
                            return
                        await asyncio.sleep(0.05)
                    raise AssertionError("Temporal timer was never recorded")

                await asyncio.wait_for(timer_persisted(), timeout=15)
                loop = asyncio.get_running_loop()

                def send_real_signal(signal_id: str):
                    # Ingress is synchronous; bridge to the Temporal client
                    # loop from a distinct thread, just like an HTTP worker.
                    future = asyncio.run_coroutine_threadsafe(
                        handle.signal("wake", signal_id), loop,
                    )
                    future.result(timeout=10)

                ingress = GitHubWebhookIngress(
                    store=store, user_id="alice", goal_id="webhook-e2e",
                    secret=b"t" * 40, signal=send_real_signal,
                )
                raw = json.dumps({
                    "action": "synchronize",
                    "number": 169,
                    "repository": {"full_name": "avaargsh/agent-control-plane"},
                    "pull_request": {"number": 169},
                }).encode()
                args = {
                    "signature": "sha256=" + hmac.new(
                        b"t" * 40, raw, hashlib.sha256,
                    ).hexdigest(),
                    "event": "pull_request",
                    "delivery": str(uuid4()),
                    "body": raw,
                }
                first = await asyncio.to_thread(ingress.accept, **args)
                duplicate = await asyncio.to_thread(ingress.accept, **args)
                result = await asyncio.wait_for(handle.result(), timeout=25)
                history = await handle.fetch_history()

        assert first == {"status": "accepted", "signal_state": "ACKED"}
        assert duplicate == {"status": "duplicate", "signal_state": "ACKED"}
        assert result["state"] == "FINISHED" and result["phase"] == "MERGED"
        assert seen == ["webhook-e2e", "webhook-e2e"]
        assert len(store.list_events(user_id="alice")) == 2
        assert any(
            event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
            for event in history.events
        )

    asyncio.run(scenario())
