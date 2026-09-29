from agent_control_plane.apply_reconciler import ApplyReconciler
from test_apply_reconciler import build_plan, executor_registry, provider_registry, PASS_GATE


GOLDEN_SLICE = {
    "runId": "run-golden-001",
    "evidenceId": "evidence-001",
    "evidenceDigest": "sha256:" + "a" * 64,
    "decisionId": "decision-sha256:" + "c" * 64,
    "releaseId": "sre-v1",
}


def reconciler():
    return ApplyReconciler(providers=provider_registry(), executors=executor_registry())


def test_release_evidence_preserves_golden_slice_causal_identity():
    result = reconciler().reconcile(
        build_plan(), eval_gates=[PASS_GATE], metrics={"accuracy": 0.95, "ece": 0.03}, golden_slice=GOLDEN_SLICE
    )
    assert result.phase == "promoted"
    assert result.evidence["golden_slice"] == GOLDEN_SLICE
    assert result.evidence["golden_slice"]["decisionId"].startswith("decision-sha256:")


def test_blocked_release_evidence_keeps_same_causal_identity():
    result = reconciler().reconcile(
        build_plan(), eval_gates=[PASS_GATE], metrics={"accuracy": 0.70, "ece": 0.03}, golden_slice=GOLDEN_SLICE
    )
    assert result.phase == "blocked"
    assert result.evidence["golden_slice"]["runId"] == GOLDEN_SLICE["runId"]
    assert result.evidence["golden_slice"]["evidenceDigest"] == GOLDEN_SLICE["evidenceDigest"]
    assert result.evidence["golden_slice"]["decisionId"] == GOLDEN_SLICE["decisionId"]
