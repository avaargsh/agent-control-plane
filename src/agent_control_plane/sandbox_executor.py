from __future__ import annotations

from typing import Any, Protocol

from .executors import ExecutionReceipt, RollbackReceipt
from .plan import ResolvedReleasePlan
from .provider_runtime import ExternalResource


class SandboxTransport(Protocol):
    def ensure_sandbox(
        self,
        *,
        claim_name: str,
        namespace: str,
        warm_pool: str,
        ttl_seconds: int | None,
        labels: dict[str, str],
    ) -> ExternalResource: ...

    def delete_sandbox(
        self,
        *,
        claim_name: str,
        namespace: str,
    ) -> dict[str, Any]: ...


class KubernetesAgentSandboxExecutor:
    provider_type = "sandbox"
    provider_name = "k8s-agent-sandbox"

    def __init__(self, transport: SandboxTransport) -> None:
        self.transport = transport

    def apply(
        self,
        *,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
        prepared: dict[str, Any],
    ) -> ExecutionReceipt:
        warm_pool = prepared.get("warm_pool")
        if not warm_pool:
            raise ValueError("Kubernetes Agent Sandbox binding requires warmPoolRef")

        labels = {
            "agentplane.io/release": plan.release_name,
            "agentplane.io/binding": binding["metadata"]["name"],
        }
        resource = self.transport.ensure_sandbox(
            claim_name=prepared["claim_name"],
            namespace=prepared["namespace"],
            warm_pool=warm_pool,
            ttl_seconds=prepared.get("ttl_seconds"),
            labels=labels,
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
                "claim_name": prepared["claim_name"],
                "namespace": prepared["namespace"],
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
        evidence = self.transport.delete_sandbox(
            claim_name=receipt.evidence["claim_name"],
            namespace=receipt.evidence["namespace"],
        )
        return RollbackReceipt(
            binding_name=receipt.binding_name,
            provider_type=receipt.provider_type,
            provider_name=receipt.provider_name,
            resource_ref=receipt.resource_ref,
            rolled_back=True,
            evidence=evidence,
        )
