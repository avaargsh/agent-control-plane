from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.frozen_evidence import FrozenEvidence
from agent_control_plane.release_evidence import verify_release_evidence
from test_apply_reconciler import PASS_GATE, build_plan, executor_registry, provider_registry


def run_release():
    approved = FrozenEvidence.capture(
        {
            "runId": "run-replay-001",
            "decisionId": "decision-001",
            "refs": {"evidenceBundle": "evidence://frozen/001"},
        }
    )
    return ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
        approved_evidence=approved,
    )


def test_promoted_release_evidence_is_sealed_for_replay():
    result = run_release()

    assert result.phase == "Promoted"
    assert result.evidence["replay_digest"].startswith("sha256:")
    assert verify_release_evidence(result.evidence)


def test_receipt_tamper_breaks_replay_verification():
    result = run_release()
    tampered = dict(result.evidence)
    tampered["receipts"] = [dict(item) for item in result.evidence["receipts"]]
    tampered["receipts"][0]["changed"] = not tampered["receipts"][0]["changed"]

    assert not verify_release_evidence(tampered)
