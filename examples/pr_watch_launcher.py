"""Trusted local service bridge for fail-closed Temporal Workflow enrollment.

A Goal registration commits a PENDING launch in the same transaction. Only a
single claimant may issue start_workflow; ambiguous ACKs are reconciled by
describing deterministic Workflow IDs, never retried blindly.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from pr_watch_control_bridge import workflow_id
from pr_watch_harness import GoalStore

class TemporalLaunchBridge:
    def __init__(
        self, *, address: str, namespace: str = "default",
        interval_seconds: int = 300,
        client_factory: Callable | None = None,
    ) -> None:
        if not address or not namespace or type(interval_seconds) is not int or not (
            30 <= interval_seconds <= 86400
        ):
            raise ValueError("invalid trusted Temporal enrollment configuration")
        self.address = address
        self.namespace = namespace
        self.interval_seconds = interval_seconds
        self._client_factory = client_factory

    async def _client(self):
        if self._client_factory is not None:
            return await self._client_factory()
        from temporalio.client import Client
        return await Client.connect(self.address, namespace=self.namespace)

    async def _describe(self, *, user_id: str, goal_id: str) -> str | None:
        from temporalio.service import RPCError, RPCStatusCode
        client = await self._client()
        handle = client.get_workflow_handle(workflow_id(user_id=user_id, goal_id=goal_id))
        try:
            details = await handle.describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return None
            raise
        # An existing execution may be closed. Existence is not RUNNING proof.
        value = details.status
        status = value.name if hasattr(value, "name") else str(value)
        if status not in {
            "RUNNING", "COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT",
        }:
            raise ValueError("unrecognized Temporal Workflow status")
        return status

    async def _start(self, *, user_id: str, goal_id: str) -> str:
        from temporalio.exceptions import WorkflowAlreadyStartedError
        from temporalio.common import WorkflowIDReusePolicy
        from temporalio.service import RPCError, RPCStatusCode
        from pr_watch_temporal import PRWatchWorkflow, TASK_QUEUE

        client = await self._client()
        spec = {
            "user_id": user_id, "goal_id": goal_id,
            "interval_seconds": self.interval_seconds,
        }
        try:
            await client.start_workflow(
                PRWatchWorkflow.run, spec,
                id=workflow_id(user_id=user_id, goal_id=goal_id),
                task_queue=TASK_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            return "ALREADY_EXISTS"
        except RPCError as exc:
            if exc.status == RPCStatusCode.ALREADY_EXISTS:
                return "ALREADY_EXISTS"
            raise
        return "START_ACK"

    def start_committed(self, store: GoalStore, *, user_id: str, goal_id: str) -> dict:
        """One start attempt, never resend IN_FLIGHT/UNKNOWN/ACKED."""
        claimed = store.claim_launch(user_id=user_id, goal_id=goal_id)
        if not claimed["claimed_now"]:
            # An IN_FLIGHT record owned by another request is NOT permission
            # to issue a second start_workflow RPC.
            return claimed
        try:
            outcome = asyncio.run(self._start(user_id=user_id, goal_id=goal_id))
            if outcome == "ALREADY_EXISTS":
                # A duplicate ID rejection proves an execution already exists
                # but does not establish whether it is still running.
                return self.reconcile(store, user_id=user_id, goal_id=goal_id)
            return store.record_launch(
                user_id=user_id, goal_id=goal_id,
                state="ACKED", evidence="START_ACK",
            )
        except Exception:
            # Transport may have accepted start before dropping ACK. Even if
            # the DB update fails, the persisted IN_FLIGHT prevents retries.
            try:
                return store.record_launch(
                    user_id=user_id, goal_id=goal_id,
                    state="UNKNOWN", evidence="START_UNCERTAIN",
                )
            except Exception:
                return store.launch(user_id=user_id, goal_id=goal_id)

    def reconcile(self, store: GoalStore, *, user_id: str, goal_id: str) -> dict:
        """Read-only Temporal describe; NOT_FOUND never authorizes retry."""
        record = store.launch(user_id=user_id, goal_id=goal_id)
        if record["state"] in ("SKIPPED", "OBSERVED"):
            return record
        try:
            status = asyncio.run(self._describe(user_id=user_id, goal_id=goal_id))
        except Exception:
            status = None
        if status is None:
            return record
        return store.record_launch(
            user_id=user_id, goal_id=goal_id,
            state="OBSERVED", evidence="DESCRIBE_EXISTS",
            workflow_status=status,
        )

    def recover_on_exclusive_startup(self, store: GoalStore, *, limit: int = 100) -> dict:
        """Single-instance startup only. Never auto-resend uncertain starts."""
        expired = store.recover_unacknowledged_launches()
        entries = store.pending_launches(limit=limit)
        attempted = observed = unresolved = 0
        for entry in entries:
            if entry["state"] == "PENDING":
                attempted += 1
                result = self.start_committed(
                    store, user_id=entry["user_id"], goal_id=entry["goal_id"],
                )
            else:
                result = self.reconcile(
                    store, user_id=entry["user_id"], goal_id=entry["goal_id"],
                )
            if result["state"] == "OBSERVED":
                observed += 1
            if result["state"] in ("PENDING", "IN_FLIGHT", "UNKNOWN"):
                unresolved += 1
        return {
            "crash_ambiguities": expired, "inspected": len(entries),
            "first_start_attempts": attempted,
            "observed_existing": observed, "unresolved": unresolved,
        }
