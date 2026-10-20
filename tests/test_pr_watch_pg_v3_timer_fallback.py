"""Real PG16 + Temporal timer fallback when a keyed control misses its wake.

Uses isolated PostgreSQL schema and a real Temporal SDK test server/Worker.
No real GitHub, public ingress, remote provider or production cluster.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("temporalio")
from psycopg import conninfo, sql  # noqa: E402
from temporalio import activity  # noqa: E402
from temporalio.api.enums.v1 import EventType  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from pr_watch_pg_migrations import PostgresMigrations  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402
from pr_watch_harness import PullSnapshot  # noqa: E402
from pr_watch_temporal import PRWatchWorkflow  # noqa: E402
from pr_watch_control_bridge import control_signal_id, workflow_id  # noqa: E402

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")
USER = "timer-owner"
SHA = "c" * 40


class ReadOnlyFixture:
    def __init__(self):
        self.reads = 0

    def fetch(self, repo, number):
        assert (repo, number) == ("o/repo", 169)
        self.reads += 1
        return PullSnapshot(head_sha=SHA, state="OPEN", ci="PENDING")


@pytest.fixture
def isolated():
    if not DSN:
        pytest.skip("requires EXCLUSIVE DISPOSABLE PR_WATCH_PG_TEST_DSN")
    schema = "test_timer_" + uuid4().hex[:16]
    with psycopg.connect(DSN) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    dsn = conninfo.make_conninfo(DSN, options=f"-c search_path={schema}")
    try:
        PostgresMigrations(dsn).upgrade(3)
        yield dsn
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.parametrize("claimed_before_crash", [False, True])
def test_real_temporal_timer_observes_durable_cancel_without_wake(
    isolated, claimed_before_crash,
):
    """Missing wake cannot hold Goal status hostage; no speculative Signal resend."""
    goal_id = "cancel-no-signal"
    request_id = ("a" if claimed_before_crash else "b") * 32
    store = PostgresWatchStore(isolated)
    store.register(user_id=USER, goal_id=goal_id, repo="o/repo", number=169)
    command = dict(
        user_id=USER, goal_id=goal_id,
        action="cancel", expected_version=1, request_id=request_id,
    )
    reader = ReadOnlyFixture()
    calls = []

    @activity.defn(name="read_and_persist_pr")
    async def observe(spec: dict) -> dict:
        assert spec["user_id"] == USER and spec["goal_id"] == goal_id
        calls.append(len(calls) + 1)
        status = await asyncio.to_thread(
            store.tick, user_id=USER, goal_id=goal_id, reader=reader,
            now=datetime.now(timezone.utc),
        )
        if len(calls) == 1:
            # The first activity already completed its observation and
            # returns RUNNING. Commit cancellation while no Signal RPC
            # occurs: this is the COMMIT-to-Signal crash window.
            assert status["state"] == "RUNNING"
            store.prepare_control(**command)
            updated, replayed = store.transition_with_request(**command)
            assert updated["state"] == "CANCELLED" and not replayed
            if claimed_before_crash:
                # Simulate crash immediately after the durable send claim,
                # without performing Temporal network I/O.
                claimed = store.claim_control_signal(
                    user_id=USER, goal_id=goal_id, version=2,
                    unattempted_only=True,
                )
                assert claimed and claimed["signal_state"] == "UNKNOWN"
            return status
        assert len(calls) == 2, "unexpected activity after terminal Goal"
        assert status["state"] == "CANCELLED"
        return status

    async def scenario():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client, task_queue="pr-watch-cancel-timer-fallback",
                workflows=[PRWatchWorkflow], activities=[observe],
                max_cached_workflows=0,
            ):
                handle = await env.client.start_workflow(
                    PRWatchWorkflow.run,
                    {"user_id": USER, "goal_id": goal_id,
                     "interval_seconds": 30},
                    id=workflow_id(user_id=USER, goal_id=goal_id),
                    task_queue="pr-watch-cancel-timer-fallback",
                )
                outcome = await asyncio.wait_for(handle.result(), timeout=30)
                history = await handle.fetch_history()
        return outcome, history

    outcome, history = asyncio.run(scenario())
    assert outcome["state"] == "CANCELLED"
    assert calls == [1, 2], "Workflow must use its unattended timer fallback"
    assert reader.reads == 1, "Cancelled Goal must not read GitHub again"
    assert len([e for e in history.events if
        e.event_type == EventType.EVENT_TYPE_TIMER_FIRED]) >= 1
    assert not [e for e in history.events if
        e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED]
    assert store.goal(user_id=USER, goal_id=goal_id)["version"] == 2
    assert store.control_request(
        user_id=USER, request_id=request_id,
    )["state"] == "COMMITTED"
    control = store.control(user_id=USER, goal_id=goal_id, version=2)
    assert (control["signal_state"], control["evidence"]) == (
        ("UNKNOWN", "ACK_UNCERTAIN") if claimed_before_crash else
        ("PENDING", "UNATTEMPTED_V3")
    )
    assert len(store.controls(user_id=USER, goal_id=goal_id)) == 1
    # This deterministic ID exists for History matching, but absence
    # of a Signal event does not authorize a blind retransmission.
    assert control_signal_id(
        user_id=USER, goal_id=goal_id, version=2, action="cancel",
    ).startswith("pr-watch-control:")
