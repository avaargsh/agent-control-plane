"""ACP TemporalSDKApi -> real Workflow Worker handoff acceptance.

Uses the Temporal SDK *development* server, a local SQLite fixture and actual
Worker A / Worker B, not live GitHub/production server persistence.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("temporalio")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agent_control_plane.runtime_clients import TerminalRuntimeConflict
from agent_control_plane.temporal_runtime_client import TemporalWorkflowClient
from agent_control_plane.temporal_sdk_api import TemporalSDKApi
from pr_watch_harness import GoalStore, PullSnapshot
from pr_watch_temporal import PRWatchWorkflow


class SnapshotReader:
    def __init__(self, snapshot: PullSnapshot) -> None:
        self.snapshot = snapshot

    def fetch(self, repo: str, number: int) -> PullSnapshot:
        assert (repo, number) == ("avaargsh/agent-control-plane", 169)
        return self.snapshot


def test_sdk_started_pr_watch_survives_worker_handoff(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = GoalStore(tmp_path / "pr-watch.db")
        goal_id = "sdk-handoff"
        store.register(
            user_id="test-user",
            goal_id=goal_id,
            repo="avaargsh/agent-control-plane",
            number=169,
        )
        calls = []
        checkpoint = asyncio.Event()

        @activity.defn(name="read_and_persist_pr")
        async def observe(spec: dict) -> dict:
            calls.append(spec["goal_id"])
            assert len(calls) <= 2, "unexpected duplicate provider observation"
            snapshot = (
                PullSnapshot("a" * 40, "OPEN", "FAILURE")
                if len(calls) == 1
                else PullSnapshot("a" * 40, "MERGED", "UNKNOWN")
            )
            result = store.tick(
                user_id=spec["user_id"],
                goal_id=spec["goal_id"],
                reader=SnapshotReader(snapshot),
                now=datetime.now(timezone.utc),
            )
            if len(calls) == 1:
                checkpoint.set()
            return result

        async with await WorkflowEnvironment.start_local() as env:
            api = TemporalSDKApi(
                target_host=env.client.service_client.config.target_host,
                namespace=env.client.namespace,
            )
            controller = TemporalWorkflowClient(api)
            workflow_id = "test-sdk/pr-watch-handoff-" + uuid.uuid4().hex
            desired = {
                "release": "pr-watch-release",
                "workflow_id": workflow_id,
                "workflow_type": "PRWatchWorkflow",
                "task_queue": "test-sdk-pr-watch-handoff",
                "input": {
                    "user_id": "test-user",
                    "goal_id": goal_id,
                    "interval_seconds": 3600,
                },
            }
            first = await asyncio.to_thread(controller.ensure_workflow, desired)
            assert first.changed is True
            assert first.evidence["status"] == "RUNNING"
            handle = env.client.get_workflow_handle(workflow_id)

            # Worker A completes one Activity and the server persists the timer
            # before we remove it. No provider observations run in Workflow code.
            async with Worker(
                env.client,
                task_queue=desired["task_queue"],
                workflows=[PRWatchWorkflow],
                activities=[observe],
                max_cached_workflows=0,
            ):
                await asyncio.wait_for(checkpoint.wait(), timeout=20)

                async def timer_persisted() -> None:
                    for _ in range(200):
                        history = await handle.fetch_history()
                        if any(
                            event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                            for event in history.events
                        ):
                            return
                        await asyncio.sleep(0.05)
                    raise AssertionError("Worker A did not persist Workflow timer")

                await asyncio.wait_for(timer_persisted(), timeout=20)

            assert len(store.list_events(user_id="test-user")) == 1
            repeated = await asyncio.to_thread(controller.ensure_workflow, desired)
            assert repeated.changed is False
            assert repeated.evidence["runId"] == first.evidence["runId"]
            assert repeated.evidence["operationId"] == first.evidence["operationId"]

            # Worker B rebuilds the same workflow state from server History.
            async with Worker(
                env.client,
                task_queue=desired["task_queue"],
                workflows=[PRWatchWorkflow],
                activities=[observe],
                max_cached_workflows=0,
            ):
                await handle.signal(PRWatchWorkflow.wake)
                result = await asyncio.wait_for(handle.result(), timeout=20)

            assert result["state"] == "FINISHED"
            assert result["phase"] == "MERGED"
            assert calls == [goal_id, goal_id]
            assert len(store.list_events(user_id="test-user")) == 2

            final = await asyncio.to_thread(api.describe, workflow_id=workflow_id)
            assert final["status"] == "COMPLETED"
            assert final["runId"] == first.evidence["runId"]
            assert final["operationId"] == first.evidence["operationId"]
            with pytest.raises(TerminalRuntimeConflict):
                await asyncio.to_thread(controller.ensure_workflow, desired)

    asyncio.run(scenario())
