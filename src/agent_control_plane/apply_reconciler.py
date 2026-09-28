from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .dependency_graph import dependency_order
from .eval_engine import EvalResult, evaluate_gate
from .executors import (
    ExecutionReceipt,
    ExecutorRegistry,
    RollbackReceipt,
)
from .plan import ResolvedReleasePlan
from .registry import ProviderRegistry
from .state_machine import ReleasePhase, ReleaseState


@dataclass(frozen=True)
class ApplyResult:
    release_name: str
    phase: str
    receipts: tuple[ExecutionReceipt, ...]
    rollback_receipts: tuple[RollbackReceipt, ...]
    eval_results: tuple[EvalResult, ...]
    evidence: Mapping[str, Any]
    error: str | None = None


class ApplyReconciler:
    """Apply a resolved release through dependency-ordered provider executors."""

    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        executors: ExecutorRegistry,
    ) -> None:
        self.providers = providers
        self.executors = executors

    def _rollback(
        self,
        *,
        plan: ResolvedReleasePlan,
        applied: list[tuple[dict[str, Any], ExecutionReceipt]],
    ) -> list[RollbackReceipt]:
        rollback_receipts: list[RollbackReceipt] = []

        for binding, receipt in reversed(applied):
            spec = binding["spec"]
            executor = self.executors.get(
                spec["type"],
                spec["provider"],
            )
            rollback_receipts.append(
                executor.rollback(
                    plan=plan,
                    binding=binding,
                    receipt=receipt,
                )
            )

        return rollback_receipts

    def reconcile(
        self,
        plan: ResolvedReleasePlan,
        *,
        eval_gates: Sequence[Mapping[str, Any]] = (),
        metrics: Mapping[str, float] | None = None,
    ) -> ApplyResult:
        state = ReleaseState(plan.release_name)
        state.transition(ReleasePhase.VALIDATED)
        state.transition(ReleasePhase.RESOLVED)

        prepared = self.providers.prepare(plan)
        order = dependency_order(plan.bindings)

        state.transition(ReleasePhase.DEPLOYING)

        receipts: list[ExecutionReceipt] = []
        applied: list[tuple[dict[str, Any], ExecutionReceipt]] = []

        try:
            for binding_name in order:
                binding = plan.bindings[binding_name]
                spec = binding["spec"]
                executor = self.executors.get(
                    spec["type"],
                    spec["provider"],
                )
                receipt = executor.apply(
                    plan=plan,
                    binding=binding,
                    prepared=prepared[binding_name],
                )
                receipts.append(receipt)
                applied.append((binding, receipt))
        except Exception as exc:
            rollback_receipts = self._rollback(
                plan=plan,
                applied=applied,
            )
            state.transition(ReleasePhase.ROLLED_BACK)

            evidence = {
                "kind": "ReleaseEvidence",
                "release": plan.release_name,
                "dependency_order": order,
                "prepared": prepared,
                "receipts": [asdict(receipt) for receipt in receipts],
                "rollback_receipts": [
                    asdict(receipt)
                    for receipt in rollback_receipts
                ],
                "eval_results": [],
                "phase": state.phase.value,
                "apply_error": str(exc),
            }

            return ApplyResult(
                release_name=plan.release_name,
                phase=state.phase.value,
                receipts=tuple(receipts),
                rollback_receipts=tuple(rollback_receipts),
                eval_results=(),
                evidence=evidence,
                error=str(exc),
            )

        state.transition(ReleasePhase.EVALUATING)

        eval_results = tuple(
            evaluate_gate(gate, metrics or {})
            for gate in eval_gates
        )

        failed = [
            (gate, result)
            for gate, result in zip(
                eval_gates,
                eval_results,
                strict=True,
            )
            if not result.passed
        ]

        rollback_receipts: list[RollbackReceipt] = []

        if not failed:
            state.transition(ReleasePhase.PROMOTED)
        else:
            actions = {
                gate.get("spec", {}).get(
                    "onFailure",
                    "block",
                )
                for gate, _ in failed
            }

            if "rollback" in actions:
                rollback_receipts = self._rollback(
                    plan=plan,
                    applied=applied,
                )
                state.transition(ReleasePhase.ROLLED_BACK)
            else:
                state.transition(ReleasePhase.BLOCKED)

        evidence = {
            "kind": "ReleaseEvidence",
            "release": plan.release_name,
            "dependency_order": order,
            "prepared": prepared,
            "receipts": [asdict(receipt) for receipt in receipts],
            "rollback_receipts": [
                asdict(receipt)
                for receipt in rollback_receipts
            ],
            "eval_results": [
                {
                    "passed": result.passed,
                    "violations": [
                        asdict(violation)
                        for violation in result.violations
                    ],
                    "on_failure": gate.get("spec", {}).get(
                        "onFailure",
                        "block",
                    ),
                }
                for gate, result in zip(
                    eval_gates,
                    eval_results,
                    strict=True,
                )
            ],
            "phase": state.phase.value,
        }

        return ApplyResult(
            release_name=plan.release_name,
            phase=state.phase.value,
            receipts=tuple(receipts),
            rollback_receipts=tuple(rollback_receipts),
            eval_results=eval_results,
            evidence=evidence,
        )
