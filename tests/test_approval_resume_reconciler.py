from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.frozen_evidence import FrozenEvidence
from test_apply_reconciler import PASS_GATE, build_plan, executor_registry, provider_registry


def test_reconciler_executes_from_approved_snapshot_not_mutated_live_evidence():
    live = {
        "runId": "run-approval-001",
        "decisionId": "decision-001",
        "severity": "critical",
        "refs": {"evidenceBundle": "evidence://frozen/001"},
    }
    approved = FrozenEvidence.capture(live)

    # Simulate the source changing while a human approval is pending.
    live["severity"] = "warning"
    live["refs"]["evidenceBundle"] = "evidence://live/reread"

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
        golden_slice=live,
        approved_evidence=approved,
    )

    assert result.phase == "promoted"
    assert result.evidence["golden_slice"]["severity"] == "critical"
    assert (
        result.evidence["golden_slice"]["refs"]["evidenceBundle"]
        == "evidence://frozen/001"
    )
