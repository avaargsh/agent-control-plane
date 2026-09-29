from __future__ import annotations

import asyncio
from typing import Any

from .provider_runtime import ExternalResource


def _run(coro):
    """Run async provider calls from the current synchronous executor boundary."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "provider transport called from a running event loop; "
        "use an async control-plane worker boundary"
    )


class TemporalSDKTransport:
    """Optional Temporal Python SDK transport."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def start_workflow(
        self,
        *,
        workflow_type: str,
        workflow_id: str,
        task_queue: str,
        input: dict[str, Any],
    ) -> ExternalResource:
        async def start():
            try:
                handle = await self.client.start_workflow(
                    workflow_type,
                    input,
                    id=workflow_id,
                    task_queue=task_queue,
                )
                return ExternalResource(
                    resource_ref=f"temporal://workflow/{workflow_id}",
                    changed=True,
                    external_refs={
                        "temporal.workflow_id": workflow_id,
                        "temporal.run_id": getattr(handle, "result_run_id", "") or "",
                    },
                    evidence={"state": "started"},
                )
            except Exception as exc:
                # Avoid importing Temporal at module import time. Detect the
                # SDK's idempotency conflict by public exception class name.
                if exc.__class__.__name__ != "WorkflowAlreadyStartedError":
                    raise
                return ExternalResource(
                    resource_ref=f"temporal://workflow/{workflow_id}",
                    changed=False,
                    external_refs={"temporal.workflow_id": workflow_id},
                    evidence={"state": "adopted-existing"},
                )
        return _run(start())

    def terminate_workflow(
        self,
        *,
        workflow_id: str,
        reason: str,
    ) -> dict[str, Any]:
        async def terminate():
            handle = self.client.get_workflow_handle(workflow_id)
            await handle.terminate(reason=reason)
            return {"state": "terminated", "workflow_id": workflow_id}
        return _run(terminate())


class AgentSandboxSDKTransport:
    """Optional Kubernetes Agent Sandbox Python SDK transport."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def ensure_sandbox(
        self,
        *,
        claim_name: str,
        namespace: str,
        warm_pool: str,
        ttl_seconds: int | None,
        labels: dict[str, str],
    ) -> ExternalResource:
        sandbox = self.client.create_sandbox(
            warmpool=warm_pool,
            namespace=namespace,
            claim_name=claim_name,
            adopt_existing=True,
            shutdown_after_seconds=ttl_seconds,
            labels=labels,
        )
        resolved_claim = getattr(sandbox, "claim_name", claim_name)
        sandbox_name = getattr(sandbox, "sandbox_name", None)
        return ExternalResource(
            resource_ref=f"k8s-sandbox://{namespace}/{resolved_claim}",
            # create_sandbox currently does not expose created-vs-adopted.
            # Conservatively mark ownership false until SDK exposes it.
            changed=False,
            external_refs={
                "sandbox.claim": resolved_claim,
                **(
                    {"sandbox.name": sandbox_name}
                    if sandbox_name
                    else {}
                ),
            },
            evidence={"state": "ready-or-adopted"},
        )

    def delete_sandbox(
        self,
        *,
        claim_name: str,
        namespace: str,
    ) -> dict[str, Any]:
        self.client.delete_sandbox(claim_name, namespace=namespace)
        return {
            "state": "deleted",
            "claim_name": claim_name,
            "namespace": namespace,
        }
