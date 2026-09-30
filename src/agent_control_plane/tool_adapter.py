from __future__ import annotations

from typing import Any

from .plan import ResolvedReleasePlan


class MCPToolAdapter:
    provider_type = "tool"
    provider_name = "mcp"

    def prepare(
        self,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        spec = binding.get("spec", {})
        config = dict(spec.get("config", {}))

        return {
            "kind": "ToolPlan",
            "provider": self.provider_name,
            "release": plan.release_name,
            "endpointRef": spec.get("endpointRef"),
            "contractRef": spec.get("toolContractRef"),
            "capabilities": list(config.get("capabilities", [])),
            "mode": config.get("mode", "read-only"),
        }
