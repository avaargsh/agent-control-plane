from __future__ import annotations

from typing import Any

from .plan import ResolvedReleasePlan


class DecisionGatewayAdapter:
    provider_type = "decision"
    provider_name = "decision-gateway"

    def prepare(
        self,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        spec = binding.get("spec", {})
        config = dict(spec.get("config", {}))
        return {
            "kind": "DecisionPlan",
            "provider": self.provider_name,
            "release": plan.release_name,
            "endpointRef": spec.get("endpointRef"),
            "decision_types": list(config.get("decisionTypes", [])),
            "fallback_binding": config.get("fallbackBinding"),
            "policy_ref": config.get("policyRef"),
        }
