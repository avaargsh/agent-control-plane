"""Optional actual Temporal workflow for persistent PR-watch reference.

Requires pip install temporalio; a Temporal server and stable shared SQLite.
This never exposes Github writes or accepts public user traffic.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError
from temporalio.worker import Worker

from pr_watch_github import GitHubReadOnlyReader
from pr_watch_harness import GoalStore, fingerprint

TASK_QUEUE = "acp-pr-watch-reference"


@activity.defn(name="read_and_persist_pr")
def read_and_persist_pr(spec: dict) -> dict:
    # Durable Workflow never performs provider I/O; Activities do.
    store = GoalStore(os.environ["PR_WATCH_DB"])
    return store.tick(
        user_id=spec["user_id"], goal_id=spec["goal_id"],
        reader=GitHubReadOnlyReader.from_environment(),
        now=datetime.now(timezone.utc),
    )


@workflow.defn
class PRWatchWorkflow:
    @workflow.run
    async def run(self, spec: dict) -> dict:
        interval = spec["interval_seconds"]
        if not isinstance(interval, int) or not 30 <= interval <= 86400:
            raise ValueError("poll interval must be between 30s and 1 day")
        for _ in range(100):
            try:
                status = await workflow.execute_activity(
                    read_and_persist_pr, spec,
                    start_to_close_timeout=timedelta(seconds=45),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=5),
                        maximum_attempts=3,
                    ),
                )
                if status["state"] in ("FINISHED", "CANCELLED"):
                    return status
            except ActivityError:
                # Read failures do not falsely complete a persistent goal.
                pass
            await workflow.sleep(interval)
        workflow.continue_as_new(spec)
        raise RuntimeError("unreachable")


async def _run(action: str, user_id: str | None, goal_id: str | None,
               interval: int, address: str, namespace: str) -> None:
    client = await Client.connect(address, namespace=namespace)
    if action == "worker":
        async with Worker(
            client, task_queue=TASK_QUEUE, workflows=[PRWatchWorkflow],
            activities=[read_and_persist_pr],
        ):
            await asyncio.Event().wait()
        return
    if not user_id or not goal_id:
        raise ValueError("start requires --user and --goal")
    goal = GoalStore(os.environ["PR_WATCH_DB"]).get(
        user_id=user_id, goal_id=goal_id,
    )
    if goal.state != "RUNNING":
        raise ValueError("goal already finished")
    spec = {"user_id": user_id, "goal_id": goal_id,
            "interval_seconds": interval}
    workflow_id = "pr-watch/" + fingerprint((user_id, goal_id))
    handle = await client.start_workflow(
        PRWatchWorkflow.run, spec, id=workflow_id, task_queue=TASK_QUEUE,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )
    print(f"Started {handle.id}; task queue {TASK_QUEUE}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Temporal PR-watch reference")
    parser.add_argument("action", choices=["worker", "start"])
    parser.add_argument("--user")
    parser.add_argument("--goal")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--address", default=os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    parser.add_argument("--namespace", default=os.getenv("TEMPORAL_NAMESPACE", "default"))
    args = parser.parse_args()
    asyncio.run(_run(args.action, args.user, args.goal, args.interval,
                     args.address, args.namespace))


if __name__ == "__main__":
    main()
