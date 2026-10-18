"""Durable local control -> Temporal wake Signal bridge, not ACP authority.

The scoped GoalStore CAS is the source of truth. A Signal is only a hint
to re-evaluate it early, never permission to create an execution or write
to GitHub. Failed/ambiguous Signal ACKs are NOT blindly sent again.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from pr_watch_harness import GoalStore, fingerprint

def control_signal_id(*, user_id: str, goal_id: str, version: int, action: str) -> str:
    return "pr-watch-control:" + fingerprint((user_id, goal_id, version, action))


def workflow_id(*, user_id: str, goal_id: str) -> str:
    return "pr-watch/" + fingerprint((user_id, goal_id))


class TemporalControlBridge:
    """Trusted server-side connection; never accept address or scope from HTTP."""

    def __init__(
        self, *, address: str, namespace: str = "default",
        client_factory: Callable | None = None,
    ) -> None:
        if not address or not namespace:
            raise ValueError("Temporal control bridge requires server configuration")
        self.address = address
        self.namespace = namespace
        self._factory = client_factory

    async def _client(self):
        if self._factory is not None:
            return await self._factory()
        from temporalio.client import Client
        return await Client.connect(self.address, namespace=self.namespace)

    async def _signal(self, *, user_id: str, goal_id: str, version: int,
                      action: str) -> str:
        from temporalio.exceptions import WorkflowNotFoundError

        client = await self._client()
        handle = client.get_workflow_handle(workflow_id(user_id=user_id, goal_id=goal_id))
        signal_id = control_signal_id(
            user_id=user_id, goal_id=goal_id, version=version, action=action,
        )
        try:
            await handle.signal("wake", signal_id)
        except WorkflowNotFoundError:
            return "NO_WORKFLOW"
        return "ACKED"

    def signal_committed(
        self, store: GoalStore, *, user_id: str, goal_id: str, version: int,
    ) -> dict:
        """Dispatch once *after* SQLite commit. Errors become UNKNOWN."""
        control = store.control(user_id=user_id, goal_id=goal_id, version=version)
        if control["signal_state"] != "PENDING":
            return control
        try:
            result = asyncio.run(self._signal(
                user_id=user_id, goal_id=goal_id, version=version,
                action=control["action"],
            ))
        except Exception:
            result = "UNKNOWN"
        # Even if this DB write fails after an accepted Signal, the journal
        # remains PENDING; explicit history reconciliation can prove acceptance.
        return store.record_control_signal(
            user_id=user_id, goal_id=goal_id, version=version,
            state=result,
            evidence={
                "ACKED": "SIGNAL_ACK",
                "NO_WORKFLOW": "NOT_FOUND",
                "UNKNOWN": "ACK_UNCERTAIN",
            }[result],
        )

    async def _history_proves(self, *, user_id: str, goal_id: str,
                              signal_id: str) -> bool:
        from temporalio.exceptions import WorkflowNotFoundError
        client = await self._client()
        handle = client.get_workflow_handle(workflow_id(user_id=user_id, goal_id=goal_id))
        try:
            history = await handle.fetch_history()
        except WorkflowNotFoundError:
            return False
        for event in history.events:
            attrs = event.workflow_execution_signaled_event_attributes
            if attrs.signal_name != "wake" or not attrs.input.payloads:
                continue
            try:
                args = await client.data_converter.decode(attrs.input.payloads)
            except Exception:
                continue
            if args == [signal_id]:
                return True
        return False

    def reconcile(
        self, store: GoalStore, *, user_id: str, goal_id: str, version: int,
    ) -> dict:
        """Read-only history proof; absence is UNKNOWN, not safe-to-retry."""
        control = store.control(user_id=user_id, goal_id=goal_id, version=version)
        if control["signal_state"] == "ACKED":
            return control
        signal_id = control_signal_id(
            user_id=user_id, goal_id=goal_id, version=version,
            action=control["action"],
        )
        try:
            found = asyncio.run(self._history_proves(
                user_id=user_id, goal_id=goal_id, signal_id=signal_id,
            ))
        except Exception:
            found = False
        if not found:
            # This run may have continued-as-new or history may be unavailable;
            # never assert the Signal was not delivered.
            return control
        return store.record_control_signal(
            user_id=user_id, goal_id=goal_id, version=version,
            state="ACKED", evidence="HISTORY_PROOF",
        )
