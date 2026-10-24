"""ACP SDK acceptance: SIGKILL an owned Temporal CLI dev-server process group.

This is a real OS-process crash with Temporal dev SQLite persistence, NOT a
production Temporal cluster/HA or server database failover. No external I/O.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("temporalio")
pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="isolated Linux process-group SIGKILL test"
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from temporalio import activity  # noqa: E402
from temporalio.api.enums.v1 import EventType  # noqa: E402
from temporalio.client import Client  # noqa: E402
from temporalio.worker import Replayer, Worker  # noqa: E402

from agent_control_plane.runtime_clients import TerminalRuntimeConflict  # noqa: E402
from agent_control_plane.temporal_runtime_client import TemporalWorkflowClient  # noqa: E402
from agent_control_plane.temporal_sdk_api import TemporalSDKApi  # noqa: E402
from pr_watch_harness import GoalStore, PullSnapshot  # noqa: E402
from pr_watch_temporal import PRWatchWorkflow  # noqa: E402


# SDK launches the official Temporal CLI dev server as a child of this
# dedicated Python supervisor. Killing the *new* process group kills both.
# A separate process lets the test survive the server's SIGKILL.
_SERVER_CHILD = r"""
import asyncio
import sys
from temporalio.testing import WorkflowEnvironment

async def main():
    async with await WorkflowEnvironment.start_local(
        dev_server_database_filename=sys.argv[1],
        dev_server_log_level="error",
    ) as env:
        address = env.client.service_client.config.target_host
        print("ACP_DEV_SERVER_READY=" + address, flush=True)
        await asyncio.Event().wait()

asyncio.run(main())
"""


async def _start_owned_server(db_file: Path):
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-c",
        _SERVER_CHILD,
        str(db_file),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert os.getpgid(proc.pid) == proc.pid, "server supervisor is not isolated"
        for _ in range(40):
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=45)
            if line.startswith(b"ACP_DEV_SERVER_READY="):
                address = line.decode().strip().split("=", 1)[1]
                assert address.startswith(("127.0.0.1:", "localhost:"))
                return proc, address
            if not line:
                raise AssertionError("Temporal dev server exited before readiness")
        raise AssertionError("Temporal dev server never reported readiness")
    except BaseException:
        await _kill_owned_server(proc)
        raise


async def _kill_owned_server(proc) -> None:
    if proc.returncode is None:
        # Only kill the process group created with start_new_session=True.
        # Never target an operator CLI, database, container, or arbitrary PID.
        try:
            if os.getpgid(proc.pid) == proc.pid:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                raise AssertionError("refusing SIGKILL of unowned process group")
        except ProcessLookupError:
            pass
    await asyncio.wait_for(proc.wait(), timeout=10)


class FixtureReader:
    def __init__(self, snapshot: PullSnapshot) -> None:
        self.snapshot = snapshot

    def fetch(self, repo: str, number: int) -> PullSnapshot:
        assert (repo, number) == ("avaargsh/agent-control-plane", 169)
        return self.snapshot


def test_temporal_cli_sigkill_preserves_acp_workflow_identity_and_history(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        temporal_db = tmp_path / "isolated-temporal-dev.db"
        goal_store = GoalStore(tmp_path / "isolated-pr-goal.db")
        goal_id = "server-sigkill"
        goal_store.register(
            user_id="test-user",
            goal_id=goal_id,
            repo="avaargsh/agent-control-plane",
            number=169,
        )
        first_observation = asyncio.Event()
        observations = []

        @activity.defn(name="read_and_persist_pr")
        async def observe(spec: dict) -> dict:
            observations.append(spec["goal_id"])
            assert len(observations) <= 2, "unexpected duplicate observation"
            snapshot = (
                PullSnapshot("a" * 40, "OPEN", "FAILURE")
                if len(observations) == 1
                else PullSnapshot("a" * 40, "MERGED", "UNKNOWN")
            )
            result = await asyncio.to_thread(
                goal_store.tick,
                user_id=spec["user_id"],
                goal_id=spec["goal_id"],
                reader=FixtureReader(snapshot),
                now=datetime.now(timezone.utc),
            )
            if len(observations) == 1:
                first_observation.set()
            return result

        queue = "acp-sigkill-" + uuid.uuid4().hex
        workflow_id = "test-sdk/cli-sigkill-" + uuid.uuid4().hex
        desired = {
            "release": "pr-watch-crash-test",
            "workflow_id": workflow_id,
            "workflow_type": "PRWatchWorkflow",
            "task_queue": queue,
            "input": {
                "user_id": "test-user",
                "goal_id": goal_id,
                "interval_seconds": 3600,
            },
        }
        server_a, address_a = await _start_owned_server(temporal_db)
        try:
            api_a = TemporalSDKApi(target_host=address_a)
            first = await asyncio.to_thread(
                TemporalWorkflowClient(api_a).ensure_workflow, desired
            )
            assert first.changed is True
            assert first.evidence["runId"]
            assert first.evidence["operationId"].startswith("sha256:")

            temporal_client_a = await Client.connect(address_a)
            handle_a = temporal_client_a.get_workflow_handle(workflow_id)
            async with Worker(
                temporal_client_a,
                task_queue=queue,
                workflows=[PRWatchWorkflow],
                activities=[observe],
                max_cached_workflows=0,
            ):
                await asyncio.wait_for(first_observation.wait(), timeout=20)

                async def wait_for_durable_history() -> None:
                    for _ in range(160):
                        events = (await handle_a.fetch_history()).events
                        if any(
                            ev.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                            for ev in events
                        ):
                            return
                        await asyncio.sleep(0.05)
                    raise AssertionError("Temporal timer was not recorded")

                await asyncio.wait_for(wait_for_durable_history(), timeout=20)

            assert len(goal_store.list_events(user_id="test-user")) == 1
            await _kill_owned_server(server_a)
            assert server_a.returncode == -signal.SIGKILL
        finally:
            await _kill_owned_server(server_a)

        assert temporal_db.exists(), "persistent Temporal database was not created"
        # Process group A was SIGKILLed, not gracefully closed. Start a new
        # Temporal CLI process against its original SQLite file.
        server_b, address_b = await _start_owned_server(temporal_db)
        try:
            client_b = await Client.connect(address_b)
            api_b = TemporalSDKApi(target_host=address_b)
            controller_b = TemporalWorkflowClient(api_b)
            recovered = await asyncio.to_thread(
                controller_b.ensure_workflow, desired
            )
            assert recovered.changed is False
            assert recovered.evidence["workflowId"] == workflow_id
            assert recovered.evidence["runId"] == first.evidence["runId"]
            assert recovered.evidence["operationId"] == first.evidence["operationId"]

            handle_b = client_b.get_workflow_handle(workflow_id)
            history = await handle_b.fetch_history()
            assert sum(
                ev.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED
                for ev in history.events
            ) == 1
            assert any(
                ev.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                for ev in history.events
            )

            async with Worker(
                client_b,
                task_queue=queue,
                workflows=[PRWatchWorkflow],
                activities=[observe],
                max_cached_workflows=0,
            ):
                await handle_b.signal(PRWatchWorkflow.wake)
                finished = await asyncio.wait_for(handle_b.result(), timeout=35)

            assert finished["state"] == "FINISHED"
            assert finished["phase"] == "MERGED"
            finished_history = await handle_b.fetch_history()
            await Replayer(workflows=[PRWatchWorkflow]).replay_workflow(
                finished_history
            )
            final = await asyncio.to_thread(api_b.describe, workflow_id=workflow_id)
            assert final["status"] == "COMPLETED"
            assert final["runId"] == first.evidence["runId"]
            assert final["operationId"] == first.evidence["operationId"]
            with pytest.raises(TerminalRuntimeConflict):
                await asyncio.to_thread(controller_b.ensure_workflow, desired)
        finally:
            await _kill_owned_server(server_b)

        assert observations == [goal_id, goal_id]
        assert len(goal_store.list_events(user_id="test-user")) == 2
        assert goal_store.get(user_id="test-user", goal_id=goal_id).state == "FINISHED"

    asyncio.run(scenario())
