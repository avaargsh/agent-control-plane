from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .plan import ResolvedReleasePlan


@dataclass(frozen=True)
class ExecutionReceipt:
    binding_name: str
    provider_type: str
    provider_name: str
    resource_ref: str
    changed: bool
    evidence: dict[str, Any]


class BindingExecutor(Protocol):
    provider_type: str
    provider_name: str

    def apply(
        self,
        *,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
        prepared: dict[str, Any],
    ) -> ExecutionReceipt:
        ...


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[tuple[str, str], BindingExecutor] = {}

    def register(self, executor: BindingExecutor) -> None:
        key = (executor.provider_type, executor.provider_name)
        if key in self._executors:
            raise ValueError(f"executor already registered: {key}")
        self._executors[key] = executor

    def get(self, provider_type: str, provider_name: str) -> BindingExecutor:
        key = (provider_type, provider_name)
        try:
            return self._executors[key]
        except KeyError as exc:
            raise KeyError(f"no executor registered for {key}") from exc


class InMemoryExecutor:
    """Deterministic executor used for local integration tests and demos."""

    def __init__(self, provider_type: str, provider_name: str) -> None:
        self.provider_type = provider_type
        self.provider_name = provider_name

    def apply(
        self,
        *,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
        prepared: dict[str, Any],
    ) -> ExecutionReceipt:
        binding_name = binding["metadata"]["name"]
        return ExecutionReceipt(
            binding_name=binding_name,
            provider_type=self.provider_type,
            provider_name=self.provider_name,
            resource_ref=(
                f"local://{self.provider_type}/{self.provider_name}/"
                f"{plan.release_name}/{binding_name}"
            ),
            changed=True,
            evidence={
                "prepared_kind": prepared.get("kind"),
                "release": plan.release_name,
            },
        )
