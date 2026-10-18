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

# Worker process intentionally never gets GitHub or Telegram credentials.
# Activity is a fixture: signals it started, then stays blocked until SIGKILL.
# Temporal server/Workflow History is in the parent test and remains alive.
CHILD = r"""
import asyncio
import os
from pathlib import Path
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker
from pr_watch_temporal import PRWatchWorkflow, TASK_QUEUE

@activity.defn(name="read_and_persist_pr")
async def blocked(spec: dict) -> dict:
    Path(os.environ["PR_WATCH_TAKEOVER_MARKER"]).write_text("activity-started")
    await asyncio.Event().wait()
    raise AssertionError("unreachable")

async def main():
    client = await Client.connect(os.environ["PR_WATCH_TEMPORAL_TEST_ADDRESS"])
    async with Worker(client, task_queue=TASK_QUEUE,
                      workflows=[PRWatchWorkflow], activities=[blocked]):
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

                assert store.goal(user_id=USER, goal_id=GOAL)["version"] == 0
                assert store.list_events(user_id=USER) == []
                child.kill()  # Real SIGKILL; no SDK graceful Worker shutdown.
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
                    # The initial Activity has a 45s start-to-close timeout.
                    # The time-skipping server does NOT infer that the killed
                    # subprocess went away; explicitly advance virtual time
                    # beyond that timeout + its 5s retry backoff.
                    await env.sleep(56)
                    result = await asyncio.wait_for(handle.result(), timeout=25)
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
    assert goal["version"] == 1
    events = store.list_events(user_id=USER)
    assert len(events) == 1
    # Re-observation under an identical selector never emits another incident.
    assert store.tick(
        user_id=USER, goal_id=GOAL, reader=Reader(),
        now=datetime.now(timezone.utc),
    )["new"] is False
    assert len(store.list_events(user_id=USER)) == 1
