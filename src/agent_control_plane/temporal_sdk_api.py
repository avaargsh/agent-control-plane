"""Optional synchronous adapter over the official asynchronous Temporal Python SDK.

Only translates the ACP provider contract. Temporal still owns Workflow
execution, history, workers, timers and retries. Does not start an Agent loop.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Mapping

from .runtime_clients import (
    RuntimeMutationOwnershipUncertain,
    RuntimeMutationUncertain,
)

_OPERATION_MEMO_KEY = "acp_operation_id"


class TemporalSDKApi:
    """One-shot SDK transport implementing `TemporalApi` for sync callers.

    Each call owns its own Client/asyncio loop. This intentionally simple
    reference is suitable for CLI/control-plane operations, not high-QPS
    serving. Do not use from an already-running asyncio event loop; use
    `asyncio.to_thread` for async services.
    """

    def __init__(
        self,
        *,
        target_host: str,
        namespace: str = "default",
        rpc_timeout_seconds: int = 15,
    ) -> None:
        if not target_host or not namespace or rpc_timeout_seconds <= 0:
            raise ValueError("Temporal target, namespace and positive timeout required")
        # This reference transport has deliberately no TLS or auth configuration.
        # Prevent it from silently sending Workflow payloads over remote plaintext.
        hostname = target_host.rpartition(":")[0].strip("[]")
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError(
                "reference Temporal SDK transport is loopback-only; "
                "use an independently authenticated TLS transport for remote clusters"
            )
        self.target_host = target_host
        self.namespace = namespace
        self.rpc_timeout = timedelta(seconds=rpc_timeout_seconds)

    def _invoke(self, operation: str, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "synchronous TemporalSDKApi cannot run inside an event loop; "
                "invoke from asyncio.to_thread"
            )
        return asyncio.run(self._invoke_async(operation, **kwargs))

    async def _invoke_async(self, operation: str, **kwargs: Any) -> Any:
        from temporalio.client import Client

        try:
            client = await Client.connect(
                self.target_host,
                namespace=self.namespace,
            )
        except Exception as exc:
            raise RuntimeMutationUncertain(
                "Temporal connection unavailable; provider outcome is unverified"
            ) from exc
        return await getattr(self, "_async_" + operation)(client, **kwargs)

    @staticmethod
    async def _describe_client(client: Any, workflow_id: str, timeout: timedelta) -> dict | None:
        from temporalio.service import RPCError, RPCStatusCode

        handle = client.get_workflow_handle(workflow_id)
        try:
            description = await handle.describe(rpc_timeout=timeout)
            operation_id = await description.memo_value(
                _OPERATION_MEMO_KEY, default=None
            )
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return None
            raise RuntimeMutationUncertain(
                "Temporal describe outcome uncertain"
            ) from exc
        status = description.status
        return {
            "workflowId": workflow_id,
            "runId": description.run_id,
            "status": status.name if status is not None else None,
            "operationId": operation_id,
        }

    def describe(self, *, workflow_id: str) -> Mapping[str, Any] | None:
        return self._invoke("describe", workflow_id=workflow_id)

    async def _async_describe(self, client: Any, *, workflow_id: str) -> dict | None:
        return await self._describe_client(client, workflow_id, self.rpc_timeout)

    def start(
        self,
        *,
        workflow_id: str,
        workflow_type: str,
        task_queue: str,
        input: Mapping[str, Any],
        operation_id: str,
    ) -> Mapping[str, Any]:
        return self._invoke(
            "start",
            workflow_id=workflow_id,
            workflow_type=workflow_type,
            task_queue=task_queue,
            input=dict(input),
            operation_id=operation_id,
        )

    async def _async_start(
        self,
        client: Any,
        *,
        workflow_id: str,
        workflow_type: str,
        task_queue: str,
        input: Mapping[str, Any],
        operation_id: str,
    ) -> dict:
        from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
        from temporalio.exceptions import WorkflowAlreadyStartedError
        from temporalio.service import RPCError

        try:
            handle = await client.start_workflow(
                workflow_type,
                dict(input),
                id=workflow_id,
                task_queue=task_queue,
                memo={_OPERATION_MEMO_KEY: operation_id},
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                rpc_timeout=self.rpc_timeout,
            )
        except (RPCError, WorkflowAlreadyStartedError) as exc:
            # A concurrent start or lost RPC response can both mean the
            # Workflow exists; the caller must re-describe and fence ownership.
            raise RuntimeMutationUncertain(
                "Temporal start unconfirmed; inspect canonical Workflow identity"
            ) from exc

        # Start ACK alone is not proof of server-visible ownership metadata.
        observed = await self._describe_client(client, workflow_id, self.rpc_timeout)
        if observed is None:
            raise RuntimeMutationUncertain(
                "Temporal accepted start but Workflow cannot yet be observed"
            )
        if handle.result_run_id and observed["runId"] != handle.result_run_id:
            raise RuntimeMutationOwnershipUncertain(
                "Temporal Workflow ID resolved to another execution after start",
                resource_ref=f"temporal://workflow/{workflow_id}",
                operation_id=operation_id,
                observed_operation_id=observed.get("operationId"),
            )
        if observed.get("operationId") != operation_id:
            raise RuntimeMutationOwnershipUncertain(
                "Temporal start did not persist expected operation identity",
                resource_ref=f"temporal://workflow/{workflow_id}",
                operation_id=operation_id,
                observed_operation_id=observed.get("operationId"),
            )
        return observed

    def terminate(
        self,
        *,
        workflow_id: str,
        reason: str,
        expected_operation_id: str | None = None,
        expected_run_id: str | None = None,
    ) -> Mapping[str, Any]:
        if not expected_operation_id or not expected_run_id:
            raise RuntimeMutationOwnershipUncertain(
                "Temporal termination requires the exact operation and Run IDs "
                "from an authorized execution receipt",
                resource_ref=f"temporal://workflow/{workflow_id}",
                operation_id=expected_operation_id,
            )
        return self._invoke(
            "terminate",
            workflow_id=workflow_id,
            reason=reason,
            expected_operation_id=expected_operation_id,
            expected_run_id=expected_run_id,
        )

    async def _async_terminate(
        self,
        client: Any,
        *,
        workflow_id: str,
        reason: str,
        expected_operation_id: str,
        expected_run_id: str,
    ) -> dict:
        from temporalio.service import RPCError

        observed = await self._describe_client(client, workflow_id, self.rpc_timeout)
        if observed is None:
            # Precondition failure, not an ambiguous post-mutation response.
            raise RuntimeMutationOwnershipUncertain(
                "Temporal termination target not found; ownership cannot be proven",
                resource_ref=f"temporal://workflow/{workflow_id}",
                operation_id=expected_operation_id,
            )
        observed_operation_id = observed.get("operationId")
        if (
            observed_operation_id != expected_operation_id
            or observed.get("runId") != expected_run_id
        ):
            raise RuntimeMutationOwnershipUncertain(
                "Temporal termination target identity differs from the "
                "authorized operation receipt",
                resource_ref=f"temporal://workflow/{workflow_id}",
                operation_id=expected_operation_id,
                observed_operation_id=observed_operation_id,
            )
        if observed.get("status") != "RUNNING":
            raise RuntimeMutationOwnershipUncertain(
                "Temporal termination requires the exact running execution",
                resource_ref=f"temporal://workflow/{workflow_id}",
                operation_id=expected_operation_id,
                observed_operation_id=observed_operation_id,
            )
        try:
            # Run-ID pinning prevents a stale Workflow ID from terminating a
            # replacement execution with a different Temporal Run ID.
            await client.get_workflow_handle(
                workflow_id, run_id=expected_run_id
            ).terminate(reason=reason, rpc_timeout=self.rpc_timeout)
        except RPCError as exc:
            raise RuntimeMutationUncertain(
                "Temporal terminate outcome uncertain"
            ) from exc
        return {
            "workflowId": workflow_id,
            "runId": expected_run_id,
            "operationId": expected_operation_id,
            "terminationRequested": True,
        }
