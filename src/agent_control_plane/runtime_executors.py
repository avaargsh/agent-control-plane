from __future__ import annotations

from typing import Any

from .executors import ExecutionReceipt, RollbackReceipt
from .plan import ResolvedReleasePlan
from .runtime_clients import (
    KubernetesRuntimeClient,
    RuntimeMutationOwnershipUncertain,
    TemporalRuntimeClient,
)


class KubernetesSandboxExecutor:
    provider_type = "sandbox"
    provider_name = "k8s-agent-sandbox"

    def __init__(self, client: KubernetesRuntimeClient) -> None:
        self.client = client

    def apply(self, *, plan: ResolvedReleasePlan, binding: dict[str, Any], prepared: dict[str, Any]) -> ExecutionReceipt:
        result = self.client.ensure_sandbox(prepared)
        return ExecutionReceipt(
            binding_name=binding["metadata"]["name"],
            provider_type=self.provider_type,
            provider_name=self.provider_name,
            resource_ref=result.resource_ref,
            changed=result.changed,
            evidence=dict(result.evidence),
        )

    def rollback(self, *, plan: ResolvedReleasePlan, binding: dict[str, Any], receipt: ExecutionReceipt) -> RollbackReceipt:
        if not receipt.changed:
            return RollbackReceipt(
                binding_name=receipt.binding_name,
                provider_type=self.provider_type,
                provider_name=self.provider_name,
                resource_ref=receipt.resource_ref,
                rolled_back=False,
                evidence={
                    "skipped": True,
                    "reason": "resource-preexisted",
                },
            )

        change_type = receipt.evidence.get("changeType")
        if change_type == "updated":
            previous = receipt.evidence.get("previousManaged")
            if not isinstance(previous, dict):
                raise RuntimeError(
                    "updated sandbox receipt is missing previous managed state"
                )
            evidence = self.client.restore_sandbox(previous)
        else:
            evidence = self.client.delete_sandbox(receipt.resource_ref)

        return RollbackReceipt(
            binding_name=receipt.binding_name,
            provider_type=self.provider_type,
            provider_name=self.provider_name,
            resource_ref=receipt.resource_ref,
            rolled_back=True,
            evidence=dict(evidence),
        )


class TemporalWorkflowExecutor:
    provider_type = "workflow"
    provider_name = "temporal"

    def __init__(self, client: TemporalRuntimeClient) -> None:
        self.client = client

    def apply(self, *, plan: ResolvedReleasePlan, binding: dict[str, Any], prepared: dict[str, Any]) -> ExecutionReceipt:
        result = self.client.ensure_workflow(prepared)
        return ExecutionReceipt(
            binding_name=binding["metadata"]["name"],
            provider_type=self.provider_type,
            provider_name=self.provider_name,
            resource_ref=result.resource_ref,
            changed=result.changed,
            evidence=dict(result.evidence),
        )

    def rollback(self, *, plan: ResolvedReleasePlan, binding: dict[str, Any], receipt: ExecutionReceipt) -> RollbackReceipt:
        if not receipt.changed:
            return RollbackReceipt(
                binding_name=receipt.binding_name,
                provider_type=self.provider_type,
                provider_name=self.provider_name,
                resource_ref=receipt.resource_ref,
                rolled_back=False,
                evidence={
                    "skipped": True,
                    "reason": "resource-preexisted",
                },
            )
        expected_operation_id = receipt.evidence.get("operationId")
        expected_run_id = receipt.evidence.get("runId")
        if (expected_operation_id is None) != (expected_run_id is None):
            raise RuntimeMutationOwnershipUncertain(
                "Temporal rollback receipt must include both operationId "
                "and runId, or neither for legacy synthetic adapters",
                resource_ref=receipt.resource_ref,
                operation_id=expected_operation_id,
            )
        if expected_operation_id is not None:
            evidence = self.client.terminate_workflow(
                receipt.resource_ref,
                expected_operation_id=expected_operation_id,
                expected_run_id=expected_run_id,
            )
        else:
            # Backward-compatible synthetic adapters; the official SDK
            # rejects unbound termination before any provider mutation.
            evidence = self.client.terminate_workflow(receipt.resource_ref)
        return RollbackReceipt(
            binding_name=receipt.binding_name,
            provider_type=self.provider_type,
            provider_name=self.provider_name,
            resource_ref=receipt.resource_ref,
            rolled_back=True,
            evidence=dict(evidence),
        )
