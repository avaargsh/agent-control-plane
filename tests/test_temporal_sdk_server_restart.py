"""ACP SDK Workflow survives a full Temporal CLI *dev server* restart.

Runs two separate official Temporal server subprocesses against one persistent
SQLite database. This does NOT represent production HA, failover, or SIGKILL.
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

from temporalio import activity  # noqa: E402
from temporalio.api.enums.v1 import EventType  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Replayer, Worker  # noqa: E402

from agent_control_plane.runtime_clients import TerminalRuntimeConflict  # noqa: E402
from agent_control_plane.temporal_runtime_client import TemporalWorkflowClient  # noqa: E402
from agent_control_plane.temporal_sdk_api import TemporalSDKApi  # noqa: E402
from pr_watch_harness import GoalStore, PullSnapshot  # noqa: E402
from pr_watch_temporal import PRWatchWorkflow  # noqa: E402


class FixtureReader:
    def __init__(self, snapshot: PullSnapshot) -> None:
        self.snapshot = snapshot

    def fetch(self, repo: str, number: int) -> PullSnapshot:
        assert (repo, number) == ("avaargsh/agent-control-plane", 169)
        return self.snapshot


def test_sdk_workflow_identity_and_history_survive_temporal_server_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        # Workflow history and the PR-Watch business store are deliberately
        # different, persistent databases. Never confuse the latter with
        # Temporal's own durable history.
        server_db = tmp_path / "temporal-dev.sqlite"
        store = GoalStore(tmp_path / "goal-store.sqlite")
        goal_id = "sdk-server-restart"
        store.register(
            user_id="test-user", goal_id=goal_id,
            repo="avaargsh/agent-control-plane", number=169,
        )
        first_committed = asyncio.Event()
        observed_calls = []

        @activity.defn(name="read_and_persist_pr")
        async def observe(spec: dict) -> dict:
            observed_calls.append(spec["goal_id"])
            assert len(observed_calls) <= 2, "unexpected duplicate provider read"
            snapshot = (
                PullSnapshot("a" * 40, "OPEN", "FAILURE")
                if len(observed_calls) == 1
                else PullSnapshot("a" * 40, "MERGED", "UNKNOWN")
            )
            result = await asyncio.to_thread(
                store.tick,
                user_id=spec["user_id"],
                goal_id=spec["goal_id"],
                reader=FixtureReader(snapshot),
                now=datetime.now(timezone.utc),
            )
            if len(observed_calls) == 1:
                first_committed.set()
            return result

        workflow_id = "test-sdk/server-restart-" + uuid.uuid4().hex
        queue = "test-sdk-server-restart"
        desired = {
            "release": "pr-watch-test-release",
            "workflow_id": workflow_id,
            "workflow_type": "PRWatchWorkflow",
            "task_queue": queue,
            "input": {
                "user_id": "test-user",
                "goal_id": goal_id,
                "interval_seconds": 3600,
            },
        }

        # Server instance A: start through ACP and persist an Activity/timer.
        async with await WorkflowEnvironment.start_local(
            dev_server_database_filename=str(server_db),
        ) as first_server:
            api_a = TemporalSDKApi(
                target_host=first_server.client.service_client.config.target_host
            )
            first = await asyncio.to_thread(
                TemporalWorkflowClient(api_a).ensure_workflow, desired
            )
            assert first.changed is True
            assert first.evidence["status"] == "RUNNING"
            assert first.evidence["operationId"].startswith("sha256:")
            handle_a = first_server.client.get_workflow_handle(workflow_id)

            async with Worker(
                first_server.client,
                task_queue=queue,
                workflows=[PRWatchWorkflow],
                activities=[observe],
                max_cached_workflows=0,
            ):
                await asyncio.wait_for(first_committed.wait(), timeout=20)

                async def await_durable_timer() -> None:
                    for _ in range(160):
                        history = await handle_a.fetch_history()
                        if any(
                            event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                            for event in history.events
                        ):
                            return
                        await asyncio.sleep(0.05)
                    raise AssertionError("timer not recorded in Temporal History")

                await asyncio.wait_for(await_durable_timer(), timeout=20)
            assert len(store.list_events(user_id="test-user")) == 1

        assert server_db.exists(), "Temporal dev database not persisted"

        # Server instance B: different Temporal process, same physical DB.
        # A successful ACP no-op requires the persisted Run and Memo to match.
        async with await WorkflowEnvironment.start_local(
            dev_server_database_filename=str(server_db),
        ) as second_server:
            api_b = TemporalSDKApi(
                target_host=second_server.client.service_client.config.target_host
            )
            controller_b = TemporalWorkflowClient(api_b)
            recovered = await asyncio.to_thread(
                controller_b.ensure_workflow, desired
            )
            assert recovered.changed is False
            assert recovered.evidence["workflowId"] == workflow_id
            assert recovered.evidence["runId"] == first.evidence["runId"]
            assert recovered.evidence["operationId"] == first.evidence["operationId"]

            handle_b = second_server.client.get_workflow_handle(workflow_id)
            history_before = await handle_b.fetch_history()
            assert sum(
                event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED
                for event in history_before.events
            ) == 1
            assert any(
                event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                for event in history_before.events
            )

            async with Worker(
                second_server.client,
                task_queue=queue,
                workflows=[PRWatchWorkflow],
                activities=[observe],
                max_cached_workflows=0,
            ):
                # The Signal is durably stored by the *restarted* server.
                await handle_b.signal(PRWatchWorkflow.wake)
                result = await asyncio.wait_for(handle_b.result(), timeout=35)

            assert result["state"] == "FINISHED"
            assert result["phase"] == "MERGED"
            history_after = await handle_b.fetch_history()
            assert sum(
                event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED
                for event in history_after.events
            ) == 1
            await Replayer(workflows=[PRWatchWorkflow]).replay_workflow(history_after)

            final = await asyncio.to_thread(api_b.describe, workflow_id=workflow_id)
            assert final["status"] == "COMPLETED"
            assert final["runId"] == first.evidence["runId"]
            assert final["operationId"] == first.evidence["operationId"]
            with pytest.raises(TerminalRuntimeConflict):
                await asyncio.to_thread(controller_b.ensure_workflow, desired)

        assert observed_calls == [goal_id, goal_id]
        assert len(store.list_events(user_id="test-user")) == 2
        assert store.get(user_id="test-user", goal_id=goal_id).state == "FINISHED"

    asyncio.run(scenario())
