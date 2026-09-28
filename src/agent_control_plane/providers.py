from __future__ import annotations

from typing import Any, Protocol

from .plan import ResolvedReleasePlan


class ProviderAdapter(Protocol):
    provider_type: str
    provider_name: str

    def prepare(
        self,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a provider-specific deployment/execution plan without executing it."""
        ...


class NoopProviderAdapter:
    provider_type = "noop"
    provider_name = "noop"

    def prepare(
        self,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "release": plan.release_name,
            "binding": binding.get("metadata", {}).get("name"),
            "provider": binding.get("spec", {}).get("provider"),
            "prepared": True,
        }
