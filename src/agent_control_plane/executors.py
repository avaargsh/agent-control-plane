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


@dataclass(frozen=True)
class RollbackReceipt:
    binding_name: str
    provider_type: str
    provider_name: str
    resource_ref: str
    rolled_back: bool
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

    def rollback(
        self,
        *,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
        receipt: ExecutionReceipt,
    ) -> RollbackReceipt:
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
    """Deterministic idempotent executor for tests and local demos."""

    def __init__(self, provider_type: str, provider_name: str) -> None:
        self.provider_type = provider_type
        self.provider_name = provider_name
        self._resources: set[str] = set()

    def _resource_ref(
        self,
        *,
        plan: ResolvedReleasePlan,
        binding_name: str,
    ) -> str:
        return (
            f"local://{self.provider_type}/{self.provider_name}/"
            f"{plan.release_name}/{binding_name}"
        )

    def apply(
        self,
        *,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
        prepared: dict[str, Any],
    ) -> ExecutionReceipt:
        binding_name = binding["metadata"]["name"]
        resource_ref = self._resource_ref(
            plan=plan,
            binding_name=binding_name,
        )
        changed = resource_ref not in self._resources

        if changed:
            self._resources.add(resource_ref)

        return ExecutionReceipt(
            binding_name=binding_name,
            provider_type=self.provider_type,
            provider_name=self.provider_name,
            resource_ref=resource_ref,
            changed=changed,
            evidence={
                "prepared_kind": prepared.get("kind"),
                "release": plan.release_name,
                "state": "created" if changed else "unchanged",
            },
        )

    def rollback(
        self,
        *,
        plan: ResolvedReleasePlan,
        binding: dict[str, Any],
        receipt: ExecutionReceipt,
    ) -> RollbackReceipt:
        existed = receipt.resource_ref in self._resources
        should_rollback = receipt.changed and existed

        if should_rollback:
            self._resources.remove(receipt.resource_ref)

        return RollbackReceipt(
            binding_name=receipt.binding_name,
            provider_type=receipt.provider_type,
            provider_name=receipt.provider_name,
            resource_ref=receipt.resource_ref,
            rolled_back=should_rollback,
            evidence={
                "release": plan.release_name,
                "reason": "local-compensation",
                "previously_changed": receipt.changed,
            },
        )
