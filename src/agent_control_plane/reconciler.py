from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .plan import ResolvedReleasePlan
from .registry import ProviderRegistry
from .state_machine import ReleasePhase, ReleaseState


@dataclass(frozen=True)
class ReconcileResult:
    release_name: str
    phase: str
    prepared: Mapping[str, dict[str, Any]]
    evidence: dict[str, Any]


class ReleaseReconciler:
    """Minimal local reconciler.

    It compiles no manifests itself; it receives a ResolvedReleasePlan, asks
    registered providers to prepare their provider-specific plans, and advances
    the release lifecycle. External mutation is deliberately outside this class.
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    def reconcile(
        self,
        plan: ResolvedReleasePlan,
        *,
        dry_run: bool = True,
    ) -> ReconcileResult:
        state = ReleaseState(plan.release_name)
        state.transition(ReleasePhase.VALIDATED)
        state.transition(ReleasePhase.RESOLVED)

        prepared = self.registry.prepare(plan)
        state.transition(ReleasePhase.DEPLOYING)

        evidence = {
            "kind": "ReleasePlanEvidence",
            "release": plan.release_name,
            "dry_run": dry_run,
            "bindings": sorted(prepared.keys()),
            "prepared": prepared,
        }

        if dry_run:
            state.transition(ReleasePhase.EVALUATING)
            state.transition(ReleasePhase.PROMOTED)
            return ReconcileResult(
                release_name=plan.release_name,
                phase=state.phase.value,
                prepared=prepared,
                evidence=evidence,
            )

        raise NotImplementedError(
            "apply mode intentionally requires real provider executors and policy gates"
        )
