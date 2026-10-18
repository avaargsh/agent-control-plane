"""Kill a real Temporal Python Worker PROCESS and prove PostgreSQL replay.

The real Temporal SDK time-skipping test server survives the Worker SIGKILL.
A second independent Worker processes the retried Activity using the same
PostgreSQL Goal; no live GitHub API, Telegram API or external Temporal cluster.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("temporalio")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from temporalio import activity  # noqa: E402
from temporalio.api.enums.v1 import EventType  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from pr_watch_control_bridge import workflow_id  # noqa: E402
from pr_watch_harness import PullSnapshot  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402
from pr_watch_temporal import PRWatchWorkflow, TASK_QUEUE  # noqa: E402

DSN = os.environ.get("PR_WATCH_PG_TEST_DSN")
USER = "temporal-takeover"
GOAL = "worker-killed"

# Worker has only a fixture-backed read-only GitHub observation; it persists
# one result before the Workflow enters its Temporal History timer. The Worker
# process is hard-killed then; no production GitHub/Telegram request occurs.
CHILD = r"""
import asyncio
import os
from pathlib import Path
from datetime import datetime, timezone
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker
from pr_watch_temporal import PRWatchWorkflow, TASK_QUEUE
from pr_watch_pg_store import PostgresWatchStore
from pr_watch_harness import PullSnapshot

class Reader:
    def fetch(self, repo, number):
        assert (repo, number) == ("o/repo", 181)
        return PullSnapshot(head_sha="b" * 40, state="OPEN", ci="FAILURE")

@activity.defn(name="read_and_persist_pr")
async def first_observation(spec: dict) -> dict:
    store = PostgresWatchStore(os.environ["PR_WATCH_PG_TEST_DSN"])
    result = await asyncio.to_thread(
        store.tick, user_id=spec["user_id"], goal_id=spec["goal_id"],
        reader=Reader(), now=datetime.now(timezone.utc),
    )
    Path(os.environ["PR_WATCH_TAKEOVER_MARKER"]).write_text("committed")
    return result

async def main():
    client = await Client.connect(os.environ["PR_WATCH_TEMPORAL_TEST_ADDRESS"])
    async with Worker(client, task_queue=TASK_QUEUE,
                      workflows=[PRWatchWorkflow], activities=[first_observation]):
        await asyncio.Event().wait()

asyncio.run(main())
"""


class Reader:
    def fetch(self, repo, number):
        assert (repo, number) == ("o/repo", 181)
        return PullSnapshot(head_sha="a" * 40, state="MERGED", ci="UNKNOWN")


async def _check_history(handle, event_type, timeout=15):
    async def watch():
        for _ in range(100):
            history = await handle.fetch_history()
            if any(ev.event_type == event_type for ev in history.events):
                return
            await asyncio.sleep(0.1)
        raise AssertionError("Temporal History event never persisted")
    await asyncio.wait_for(watch(), timeout=timeout)


def test_os_worker_sigkill_and_temporal_activity_redelivery(tmp_path):
    if not DSN:
        pytest.skip("requires disposable PostgreSQL 16")
    store = PostgresWatchStore(DSN)
    store.install_v2()
    store.register(user_id=USER, goal_id=GOAL, repo="o/repo", number=181)

    async def scenario():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            address = env.client.service_client.config.target_host
            marker = tmp_path / "worker-active"
            child_env = os.environ.copy()
            child_env.update({
                "PR_WATCH_TEMPORAL_TEST_ADDRESS": address,
                "PR_WATCH_TAKEOVER_MARKER": str(marker),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "examples"),
            })
            child = subprocess.Popen(
                [sys.executable, "-u", "-c", CHILD],
                env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                handle = await env.client.start_workflow(
                    PRWatchWorkflow.run,
                    {"user_id": USER, "goal_id": GOAL, "interval_seconds": 30},
                    id=workflow_id(user_id=USER, goal_id=GOAL),
                    task_queue=TASK_QUEUE,
                )
                async def wait_for_marker():
                    for _ in range(120):
                        if marker.exists():
                            return
                        if child.poll() is not None:
                            _, err = await asyncio.to_thread(child.communicate)
                            raise AssertionError(
                                "first Worker failed to launch: " +
                                err.decode(errors="replace")[-1500:]
                            )
                        await asyncio.sleep(0.1)
                    raise AssertionError("first Worker did not begin Activity")
                await asyncio.wait_for(wait_for_marker(), timeout=20)

                # The Activity committed its observation, but the
                # Workflow History must also durably record TIMER_STARTED
                # before Worker A disappears. This pins the crash to a
                # deterministic lifecycle point, not a scheduling race.
                await _check_history(handle, EventType.EVENT_TYPE_TIMER_STARTED)
                assert store.goal(user_id=USER, goal_id=GOAL)["version"] == 1
                assert len(store.list_events(user_id=USER)) == 1
                child.kill()  # Real SIGKILL; no graceful Worker shutdown.
                await asyncio.to_thread(child.wait, timeout=5)

                @activity.defn(name="read_and_persist_pr")
                async def recovered(spec: dict) -> dict:
                    return await asyncio.to_thread(
                        store.tick, user_id=spec["user_id"],
                        goal_id=spec["goal_id"], reader=Reader(),
                        now=datetime.now(timezone.utc),
                    )

                async with Worker(
                    env.client, task_queue=TASK_QUEUE,
                    workflows=[PRWatchWorkflow], activities=[recovered],
                    max_cached_workflows=0,
                ):
                    # A durable wake signal causes the replacement Worker
                    # to replay History and run the next observation.
                    # This tests OS-process handoff after a committed
                    # Activity, not Activity-timeout redelivery in mid-RPC.
                    await handle.signal(PRWatchWorkflow.wake)
                    # A killed worker does not release its sticky task queue
                    # gracefully. Give Temporal its real schedule-to-start
                    # timeout before requiring Worker B to poll the normal
                    # queue; this is not an Activity execution retry.
                    await asyncio.sleep(12)
                    try:
                        result = await asyncio.wait_for(handle.result(), timeout=35)
                    except TimeoutError:
                        history = await handle.fetch_history()
                        print(
                            "TAKEOVER_HISTORY:",
                            [item.event_type.name for item in history.events][-18:],
                            "PG_STATE:", store.goal(user_id=USER, goal_id=GOAL),
                            flush=True,
                        )
                        raise
                assert result["state"] == "FINISHED"
                assert result["phase"] == "MERGED"
                history = await handle.fetch_history()
                starts = [
                    event for event in history.events
                    if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED
                ]
                assert len(starts) == 1
            finally:
                if child.poll() is None:
                    child.kill()
                await asyncio.to_thread(child.communicate, timeout=5)

    asyncio.run(scenario())
    goal = store.goal(user_id=USER, goal_id=GOAL)
    assert goal["state"] == "FINISHED"
    assert goal["version"] == 2
    events = store.list_events(user_id=USER)
    assert len(events) == 2
    assert [e["kind"] for e in events] == ["ACTION_REQUIRED", "TASK_SUCCEEDED"]
    assert store.delivery_counts(user_id=USER) == {"PENDING": 1}
    # Re-observation under an identical selector never emits another incident.
    assert store.tick(
        user_id=USER, goal_id=GOAL, reader=Reader(),
        now=datetime.now(timezone.utc),
    )["new"] is False
    assert len(store.list_events(user_id=USER)) == 2
