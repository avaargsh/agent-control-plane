from __future__ import annotations

from typing import Any

from .plan import ResolvedReleasePlan


class CodexHarnessAdapter:
    provider_type = "harness"
    provider_name = "codex"

    def prepare(
        self,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "kind": "HarnessPlan",
            "provider": self.provider_name,
            "release": plan.release_name,
            "session_mode": "canonical-session-ref",
            "config": dict(binding.get("spec", {}).get("config", {})),
        }


class TemporalWorkflowAdapter:
    provider_type = "workflow"
    provider_name = "temporal"

    def prepare(
        self,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        spec = binding.get("spec", {})
        return {
            "kind": "WorkflowPlan",
            "provider": self.provider_name,
            "release": plan.release_name,
            "endpointRef": spec.get("endpointRef"),
            "workflow_type": spec.get("config", {}).get(
                "workflowType",
                "AgentRunWorkflow",
            ),
        }


class KubernetesSandboxAdapter:
    provider_type = "sandbox"
    provider_name = "k8s-agent-sandbox"

    def prepare(
        self,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        config = dict(binding.get("spec", {}).get("config", {}))
        binding_name = binding["metadata"]["name"]
        warm_pool = config.get("warmPoolRef")
        if warm_pool is None and config.get("warmPool"):
            warm_pool = "default"
        return {
            "kind": "SandboxPlan",
            "provider": self.provider_name,
            "release": plan.release_name,
            "claim_name": config.get(
                "claimName",
                f"{plan.release_name}-{binding_name}".lower().replace("_", "-"),
            ),
            "namespace": config.get("namespace", "default"),
            "isolation": config.get("runtimeClass", config.get("isolation", "gvisor")),
            "warm_pool": warm_pool,
            "ttl_seconds": config.get("ttlSeconds"),
            "placement": dict(plan.placement),
        }
