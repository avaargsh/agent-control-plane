from agent_control_plane.apply_reconciler import ApplyReconciler
from test_apply_reconciler import RECOVERY_GATE, build_plan, executor_registry, provider_registry


GOLDEN_SLICE = {
    "runId": "run-golden-001",
    "workflowId": "wf-golden-001",
    "evidenceId": "evidence-001",
    "evidenceDigest": "sha256:" + "a" * 64,
    "decisionId": "decision-sha256:" + "c" * 64,
    "releaseId": "sre-v1",
}


def test_golden_slice_closes_execute_verify_promote_chain():
    result = ApplyReconciler(
        providers=provider_registry(), executors=executor_registry()
    ).reconcile(
        build_plan(),
        golden_slice=GOLDEN_SLICE,
        eval_gates=[RECOVERY_GATE],
        recovery_evidence={
            "success": True,
            "identityPreserved": True,
            "sourceSandboxRef": "sandbox://cell-a/old",
            "targetSandboxRef": "sandbox://cell-b/new",
            "snapshotRef": "snapshot://old",
            "verificationEvidenceRef": "evidence://recovery/verified-001",
            "verificationWindowSeconds": 300,
        },
    )
    assert result.phase == "promoted"
    assert result.evidence["golden_slice"] == GOLDEN_SLICE
    assert len(result.evidence["receipts"]) == 5
    assert result.evidence["recovery"]["verificationEvidenceRef"] == "evidence://recovery/verified-001"
    assert result.evidence["recovery"]["verificationWindowSeconds"] == 300
    assert result.evidence["eval_results"][0]["passed"] is True


def test_golden_slice_closes_execute_verify_rollback_chain():
    result = ApplyReconciler(
        providers=provider_registry(), executors=executor_registry()
    ).reconcile(
        build_plan(),
        golden_slice=GOLDEN_SLICE,
        eval_gates=[RECOVERY_GATE],
        recovery_evidence={
            "success": False,
            "identityPreserved": True,
            "verificationEvidenceRef": "evidence://recovery/failed-001",
            "verificationWindowSeconds": 300,
        },
    )
    assert result.phase == "rolled_back"
    assert result.evidence["golden_slice"]["decisionId"] == GOLDEN_SLICE["decisionId"]
    assert result.evidence["recovery"]["verificationEvidenceRef"] == "evidence://recovery/failed-001"
    assert result.evidence["eval_results"][0]["passed"] is False
    assert len(result.evidence["rollback_receipts"]) == 5
