from __future__ import annotations

from typing import Any

from .capabilities import ProviderCapabilities, check_conformance
from .plan import ResolvedReleasePlan
from .providers import ProviderAdapter


class ProviderRegistry:
    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], ProviderAdapter] = {}

    def register(self, adapter: ProviderAdapter) -> None:
        key = (adapter.provider_type, adapter.provider_name)
        if key in self._adapters:
            raise ValueError(f"provider adapter already registered: {key}")
        self._adapters[key] = adapter

    def get(self, provider_type: str, provider_name: str) -> ProviderAdapter:
        key = (provider_type, provider_name)
        try:
            return self._adapters[key]
        except KeyError as exc:
            raise KeyError(f"no provider adapter registered for {key}") from exc

    def conformance(self, plan: ResolvedReleasePlan) -> dict[str, object]:
        results = {}
        for binding_name, binding in plan.bindings.items():
            spec = binding["spec"]
            adapter = self.get(spec["type"], spec["provider"])
            supported = getattr(adapter, "capabilities", None)
            # Backward-compatible adapters with no declaration can only satisfy
            # bindings that require no explicit capabilities.
            provider = ProviderCapabilities(
                provider_type=spec["type"],
                provider_name=spec["provider"],
                capabilities=frozenset(supported or ()),
            )
            results[binding_name] = check_conformance(
                required=spec.get("capabilities", ()),
                provider=provider,
            )
        return results

    def prepare(self, plan: ResolvedReleasePlan) -> dict[str, dict[str, Any]]:
        prepared: dict[str, dict[str, Any]] = {}

        for binding_name, binding in plan.bindings.items():
            spec = binding["spec"]
            adapter = self.get(spec["type"], spec["provider"])
            prepared[binding_name] = adapter.prepare(plan, binding)

        return prepared
