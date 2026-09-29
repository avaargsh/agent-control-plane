from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .apply_reconciler import ApplyReconciler, ApplyResult
from .plan import ResolvedReleasePlan


@dataclass(frozen=True)
class RecoveryGoldenRun:
    observed_placement: str
    recovery_evidence: Mapping[str, Any]


RECOVERY_GATE = {
    "spec": {
        "conditions": [
            {
                "metric": "recovery_success",
                "op": "eq",
                "value": 1.0,
            },
            {
                "metric": "recovery_identity_preserved",
                "op": "eq",
                "value": 1.0,
            },
        ],
        "onFailure": "rollback",
    }
}


def run_recovery_golden_slice(
    reconciler: ApplyReconciler,
    plan: ResolvedReleasePlan,
    run: RecoveryGoldenRun,
) -> ApplyResult:
    """Exercise placement + recovery evidence without importing runtime internals."""
    return reconciler.reconcile(
        plan,
        observed_placement=run.observed_placement,
        recovery_evidence=run.recovery_evidence,
        eval_gates=[RECOVERY_GATE],
    )
