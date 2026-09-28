from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .eval_engine import EvalResult, evaluate_gate
from .executors import ExecutionReceipt, ExecutorRegistry
from .plan import ResolvedReleasePlan
from .registry import ProviderRegistry
from .state_machine import ReleasePhase, ReleaseState


@dataclass(frozen=True)
class ApplyResult:
    release_name: str
    phase: str
    receipts: tuple[ExecutionReceipt, ...]
    eval_results: tuple[EvalResult, ...]
    evidence: Mapping[str, Any]


class ApplyReconciler:
    """Apply a resolved release through explicit provider executors.

    Provider preparation, provider mutation, and evaluation remain separate steps.
    A release is promoted only after every configured evaluation gate passes.
    """

    _TYPE_ORDER = {
        "sandbox": 10,
        "tool": 20,
        "model": 30,
        "context": 40,
        "memory": 50,
        "harness": 60,
        "workflow": 70,
        "decision": 80,
        "traffic": 90,
    }

    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        executors: ExecutorRegistry,
    ) -> None:
        self.providers = providers
        self.executors = executors

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
        state.transition(ReleasePhase.DEPLOYING)

        ordered_bindings = sorted(
            plan.bindings.items(),
            key=lambda item: (
                self._TYPE_ORDER.get(item[1]["spec"]["type"], 100),
                item[0],
            ),
        )

        receipts: list[ExecutionReceipt] = []
        for binding_name, binding in ordered_bindings:
            spec = binding["spec"]
            executor = self.executors.get(
                spec["type"],
                spec["provider"],
            )
            receipts.append(
                executor.apply(
                    plan=plan,
                    binding=binding,
                    prepared=prepared[binding_name],
                )
            )

        state.transition(ReleasePhase.EVALUATING)

        eval_results = tuple(
            evaluate_gate(gate, metrics or {})
            for gate in eval_gates
        )

        if any(not result.passed for result in eval_results):
            state.transition(ReleasePhase.BLOCKED)
        else:
            state.transition(ReleasePhase.PROMOTED)

        evidence = {
            "kind": "ReleaseEvidence",
            "release": plan.release_name,
            "prepared": prepared,
            "receipts": [asdict(receipt) for receipt in receipts],
            "eval_results": [
                {
                    "passed": result.passed,
                    "violations": [
                        asdict(violation)
                        for violation in result.violations
                    ],
                }
                for result in eval_results
            ],
            "phase": state.phase.value,
        }

        return ApplyResult(
            release_name=plan.release_name,
            phase=state.phase.value,
            receipts=tuple(receipts),
            eval_results=eval_results,
            evidence=evidence,
        )
