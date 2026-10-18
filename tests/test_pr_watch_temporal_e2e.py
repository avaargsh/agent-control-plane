"""Integration acceptance against the Temporal SDK's real time-skipping server.

Not a mocked workflow engine. This process uses a real Temporal test server,
worker polling and workflow history, with a fixture-only GitHub read Activity.
It does NOT authenticate to a live GitHub repository or prove production HA.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# The normal fast suite has no Temporal SDK dependency; dedicated CI installs it.
pytest.importorskip("temporalio")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from attention_plane import AttentionOutbox, DeliveryState
from pr_watch_harness import GoalStore, PullSnapshot
from pr_watch_temporal import PRWatchWorkflow

SHA = "b" * 40
QUEUE = "pr-watch-integration-test"


class FixtureReader:
    def __init__(self, snapshot: PullSnapshot) -> None:
        self.snapshot = snapshot

    def fetch(self, repo: str, number: int) -> PullSnapshot:
        assert (repo, number) == ("avaargsh/agent-control-plane", 169)
        return self.snapshot


def setup(db_path: Path, goal_id: str) -> GoalStore:
    store = GoalStore(db_path)
    store.register(
        user_id="user-1", goal_id=goal_id,
        repo="avaargsh/agent-control-plane", number=169,
    )
    return store


def poll(store: GoalStore, goal_id: str, snapshot: PullSnapshot) -> dict:
    return store.tick(
        user_id="user-1", goal_id=goal_id, reader=FixtureReader(snapshot),
        now=datetime.now(timezone.utc),
    )


def test_real_temporal_ci_failure_recovery_and_merge(tmp_path):
    async def scenario() -> None:
        store = setup(tmp_path / "watch.db", "lifecycle")
        readings = [
            PullSnapshot(SHA, "OPEN", "FAILURE"),
            PullSnapshot(SHA, "OPEN", "SUCCESS"),
            PullSnapshot(SHA, "MERGED", "UNKNOWN"),
        ]
        calls = []

        @activity.defn(name="read_and_persist_pr")
        async def observe(spec: dict) -> dict:
            calls.append(spec["goal_id"])
            if len(calls) > len(readings):
                raise AssertionError("unexpected read after final PR state")
            return poll(store, spec["goal_id"], readings[len(calls) - 1])

        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client, task_queue=QUEUE,
                workflows=[PRWatchWorkflow], activities=[observe],
            ):
                result = await asyncio.wait_for(
                    env.client.execute_workflow(
                        PRWatchWorkflow.run,
                        {"user_id": "user-1", "goal_id": "lifecycle",
                         "interval_seconds": 30},
                        id="pr-watch-temporal-lifecycle", task_queue=QUEUE,
                    ), timeout=25,
                )
        assert result["state"] == "FINISHED" and result["phase"] == "MERGED"
        assert len(calls) == 3
        assert len(store.list_events(user_id="user-1")) == 3
        # CI failure needs user action, but green and merge are digested.
        assert AttentionOutbox(store.path).count(
            user_id="user-1", state=DeliveryState.PENDING,
        ) == 1

    asyncio.run(scenario())


def test_real_temporal_restarts_worker_with_durable_state(tmp_path):
    async def scenario() -> None:
        store = setup(tmp_path / "watch.db", "restart")
        first_finished = asyncio.Event()
        calls = []

        @activity.defn(name="read_and_persist_pr")
        async def observe(spec: dict) -> dict:
            calls.append(spec["goal_id"])
            snapshot = (
                PullSnapshot(SHA, "OPEN", "FAILURE") if len(calls) == 1
                else PullSnapshot(SHA, "MERGED", "UNKNOWN")
            )
            result = poll(store, spec["goal_id"], snapshot)
            if len(calls) == 1:
                first_finished.set()
            return result

        async with await WorkflowEnvironment.start_time_skipping() as env:
            handle = await env.client.start_workflow(
                PRWatchWorkflow.run,
                {"user_id": "user-1", "goal_id": "restart",
                 "interval_seconds": 30},
                id="pr-watch-temporal-worker-restart", task_queue=QUEUE,
            )
            # Worker 1 observes CI failure and then shuts down. Its in-memory
            # workflow and activity state must not be needed by worker 2.
            async with Worker(
                env.client, task_queue=QUEUE,
                workflows=[PRWatchWorkflow], activities=[observe],
                max_cached_workflows=0,
            ):
                await asyncio.wait_for(first_finished.wait(), timeout=15)
                # The Activity can persist the DB before Temporal confirms
                # completion. Wait for a durable Workflow History TIMER_STARTED
                # rather than shutting down the old worker at a racey instant.
                async def wait_for_timer() -> None:
                    for _ in range(120):
                        history = await handle.fetch_history()
                        if any(
                            item.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                            for item in history.events
                        ):
                            return
                        await asyncio.sleep(0.05)
                    raise AssertionError("workflow did not persist its timer")

                await asyncio.wait_for(wait_for_timer(), timeout=15)
            assert store.get(user_id="user-1", goal_id="restart").phase == "CI_BLOCKED"
            async with Worker(
                env.client, task_queue=QUEUE,
                workflows=[PRWatchWorkflow], activities=[observe],
                max_cached_workflows=0,
            ):
                # Signal is recorded by Temporal, so the new Worker can
                # resume immediately without a timing-sensitive fake sleep.
                await handle.signal(PRWatchWorkflow.wake)
                result = await asyncio.wait_for(handle.result(), timeout=25)

        assert result["state"] == "FINISHED"
        assert calls == ["restart", "restart"]
        assert len(store.list_events(user_id="user-1")) == 2
        assert AttentionOutbox(store.path).count(
            user_id="user-1", state=DeliveryState.PENDING,
        ) == 1

    asyncio.run(scenario())


def test_real_temporal_failed_activity_does_not_complete_goal(tmp_path):
    async def scenario() -> None:
        store = setup(tmp_path / "watch.db", "read-outage")
        attempts = 0

        @activity.defn(name="read_and_persist_pr")
        async def observe(spec: dict) -> dict:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ApplicationError("fixture provider outage", non_retryable=True)
            if attempts > 2:
                raise AssertionError("unexpected repeated provider read")
            return poll(store, spec["goal_id"], PullSnapshot(SHA, "MERGED", "UNKNOWN"))

        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client, task_queue=QUEUE,
                workflows=[PRWatchWorkflow], activities=[observe],
            ):
                result = await asyncio.wait_for(
                    env.client.execute_workflow(
                        PRWatchWorkflow.run,
                        {"user_id": "user-1", "goal_id": "read-outage",
                         "interval_seconds": 30},
                        id="pr-watch-temporal-outage-recovery", task_queue=QUEUE,
                    ), timeout=25,
                )
        assert result["state"] == "FINISHED"
        assert attempts == 2
        assert len(store.list_events(user_id="user-1")) == 1

    asyncio.run(scenario())


def test_real_temporal_api_cancelled_goal_stops_without_provider_read(tmp_path):
    async def scenario() -> None:
        store = setup(tmp_path / "watch.db", "cancelled")
        calls = []

        @activity.defn(name="read_and_persist_pr")
        async def observe(spec: dict) -> dict:
            calls.append(spec["goal_id"])
            if len(calls) == 1:
                result = poll(
                    store, spec["goal_id"], PullSnapshot(SHA, "OPEN", "PENDING"),
                )
                # Simulates successful, authenticated API CAS control.
                goal = store.get(user_id="user-1", goal_id="cancelled")
                store.transition(
                    user_id="user-1", goal_id="cancelled",
                    action="cancel", expected_version=goal.version,
                )
                return result
            return store.tick(
                user_id="user-1", goal_id="cancelled",
                reader=FixtureReader(PullSnapshot(SHA, "MERGED", "UNKNOWN")),
                now=datetime.now(timezone.utc),
            )

        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client, task_queue=QUEUE,
                workflows=[PRWatchWorkflow], activities=[observe],
            ):
                result = await asyncio.wait_for(
                    env.client.execute_workflow(
                        PRWatchWorkflow.run,
                        {"user_id": "user-1", "goal_id": "cancelled",
                         "interval_seconds": 30},
                        id="pr-watch-temporal-cancelled", task_queue=QUEUE,
                    ), timeout=25,
                )
        assert result["state"] == "CANCELLED"
        assert calls == ["cancelled", "cancelled"]
        assert store.get(user_id="user-1", goal_id="cancelled").state == "CANCELLED"

    asyncio.run(scenario())
