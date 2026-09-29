from __future__ import annotations

from typing import Any

from .executors import ExecutionReceipt, RollbackReceipt
from .plan import ResolvedReleasePlan
from .provider_runtime import TemporalTransport


class TemporalWorkflowExecutor:
    provider_type = "workflow"
    provider_name = "temporal"

    def __init__(self, transport: TemporalTransport) -> None:
        self.transport = transport

    def apply(
        self,
        *,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
        prepared: dict[str, Any],
    ) -> ExecutionReceipt:
        workflow_id = prepared["workflow_id"]
        resource = self.transport.start_workflow(
            workflow_type=prepared["workflow_type"],
            workflow_id=workflow_id,
            task_queue=prepared["task_queue"],
            input={
                "release": plan.release_name,
                "bundle": plan.bundle_name,
                "binding": binding["metadata"]["name"],
            },
        )
        return ExecutionReceipt(
            binding_name=binding["metadata"]["name"],
            provider_type=self.provider_type,
            provider_name=self.provider_name,
            resource_ref=resource.resource_ref,
            changed=resource.changed,
            evidence={
                **resource.evidence,
                "external_refs": resource.external_refs,
                "workflow_id": workflow_id,
            },
        )

    def rollback(
        self,
        *,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
        receipt: ExecutionReceipt,
    ) -> RollbackReceipt:
        if not receipt.changed:
            return RollbackReceipt(
                binding_name=receipt.binding_name,
                provider_type=receipt.provider_type,
                provider_name=receipt.provider_name,
                resource_ref=receipt.resource_ref,
                rolled_back=False,
                evidence={"reason": "resource-preexisted"},
            )
        evidence = self.transport.terminate_workflow(
            workflow_id=receipt.evidence["workflow_id"],
            reason=f"control-plane rollback for {plan.release_name}",
        )
        return RollbackReceipt(
            binding_name=receipt.binding_name,
            provider_type=receipt.provider_type,
            provider_name=receipt.provider_name,
            resource_ref=receipt.resource_ref,
            rolled_back=True,
            evidence=evidence,
        )
