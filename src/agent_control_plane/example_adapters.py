from __future__ import annotations

from typing import Any

from .plan import ResolvedReleasePlan


class OpenAIAgentsHarnessAdapter:
    provider_type = "harness"
    provider_name = "openai-agents"

    def prepare(
        self,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        spec = binding.get("spec", {})
        config = dict(spec.get("config", {}))
        return {
            "kind": "HarnessPlan",
            "provider": self.provider_name,
            "release": plan.release_name,
            "manifest_ref": config.get("manifestRef"),
            "capabilities": list(spec.get("capabilities", [])),
            "ownership": dict(spec.get("ownership", {})),
            "config": config,
        }


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
            "task_queue": spec.get("config", {}).get(
                "taskQueue",
                "agent-runtime",
            ),
            "workflow_id": (
                f"{plan.release_name}:"
                f"{binding.get('metadata', {}).get('name', 'workflow')}"
            ),
            "ownership": dict(spec.get("ownership", {})),
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
        binding_name = binding.get("metadata", {}).get("name", "sandbox")
        warm_pool = config.get("warmPoolRef") or config.get("warmPool")
        if isinstance(warm_pool, bool):
            warm_pool = "default" if warm_pool else None
        return {
            "kind": "SandboxPlan",
            "provider": self.provider_name,
            "release": plan.release_name,
            "claim_name": config.get(
                "claimName",
                f"{plan.release_name}-{binding_name}".lower().replace("_", "-"),
            ),
            "namespace": config.get("namespace", "default"),
            "warm_pool": warm_pool,
            "ttl_seconds": config.get("ttlSeconds"),
            "isolation": config.get(
                "runtimeClass",
                config.get("isolation", "gvisor"),
            ),
            "placement": dict(plan.placement),
        }
