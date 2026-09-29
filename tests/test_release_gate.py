from agent_control_plane.release_evidence import seal_release_evidence
from agent_control_plane.release_gate import evaluate_release_gate


GATE = {
    "spec": {
        "evidenceRequired": True,
        "requiredProvenance": ["operation"],
        "onFailure": "block",
        "conditions": [
            {"metric": "recovery_success_rate", "op": "gte", "value": 1.0},
            {"metric": "false_automation_rate", "op": "eq", "value": 0.0},
        ],
    }
}


def evidence():
    return seal_release_evidence({
        "release_ref": "checkout-sre-golden-v1",
        "runtime_run_id": "run-001",
        "bundle_sha256": "abc",
        "remediation_status": "VERIFIED",
        "post_action_ref": "artifact://run-001/post-action-evidence.json",
        "operation_id": "op-001",
        "operation_phase": "VERIFIED",
        "operation_evidence_refs": ["k8s://ready/4", "prometheus://latency/0.18"],
    })


def test_promote_requires_valid_evidence_and_passing_eval():
    result = evaluate_release_gate(
        GATE,
        {"recovery_success_rate": 1.0, "false_automation_rate": 0.0},
        evidence(),
    )
    assert result.passed is True
    assert result.decision == "PROMOTE"
    assert result.release_ref == "checkout-sre-golden-v1"


def test_mutated_evidence_blocks_promotion():
    sealed = evidence()
    sealed["remediation_status"] = "ROLLED_BACK"
    result = evaluate_release_gate(
        GATE,
        {"recovery_success_rate": 1.0, "false_automation_rate": 0.0},
        sealed,
    )
    assert result.passed is False
    assert result.decision == "BLOCK"
    assert result.reason == "INVALID_RELEASE_EVIDENCE"


def test_failed_eval_blocks_even_with_valid_evidence():
    result = evaluate_release_gate(
        GATE,
        {"recovery_success_rate": 0.0, "false_automation_rate": 0.0},
        evidence(),
    )
    assert result.passed is False
    assert result.decision == "BLOCK"
    assert result.reason == "EVAL_GATE_FAILED"


def test_missing_operation_identity_blocks_promotion():
    sealed = seal_release_evidence({
        "release_ref": "checkout-sre-golden-v1",
        "runtime_run_id": "run-001",
        "bundle_sha256": "abc",
        "remediation_status": "VERIFIED",
        "post_action_ref": "artifact://run-001/post-action-evidence.json",
        "operation_phase": "VERIFIED",
        "operation_evidence_refs": ["k8s://ready/4"],
    })
    result = evaluate_release_gate(
        GATE,
        {"recovery_success_rate": 1.0, "false_automation_rate": 0.0},
        sealed,
    )
    assert result.reason == "MISSING_OPERATION_ID"


def test_non_verified_operation_blocks_promotion():
    sealed = seal_release_evidence({
        "release_ref": "checkout-sre-golden-v1",
        "runtime_run_id": "run-001",
        "bundle_sha256": "abc",
        "remediation_status": "VERIFICATION_FAILED",
        "post_action_ref": "artifact://run-001/post-action-evidence.json",
        "operation_id": "op-001",
        "operation_phase": "VERIFICATION_FAILED",
        "operation_evidence_refs": ["k8s://ready/4"],
    })
    result = evaluate_release_gate(
        GATE,
        {"recovery_success_rate": 1.0, "false_automation_rate": 0.0},
        sealed,
    )
    assert result.reason == "OPERATION_NOT_VERIFIED"


def test_missing_operation_evidence_blocks_promotion():
    sealed = seal_release_evidence({
        "release_ref": "checkout-sre-golden-v1",
        "runtime_run_id": "run-001",
        "bundle_sha256": "abc",
        "remediation_status": "VERIFIED",
        "post_action_ref": "artifact://run-001/post-action-evidence.json",
        "operation_id": "op-001",
        "operation_phase": "VERIFIED",
        "operation_evidence_refs": [],
    })
    result = evaluate_release_gate(
        GATE,
        {"recovery_success_rate": 1.0, "false_automation_rate": 0.0},
        sealed,
    )
    assert result.reason == "MISSING_OPERATION_EVIDENCE"


def test_evidence_required_does_not_imply_operation_provenance():
    gate = {
        "spec": {
            "suiteRef": "generic-smoke",
            "evidenceRequired": True,
            "onFailure": "block",
            "conditions": [
                {"metric": "recovery_success_rate", "op": "gte", "value": 1.0},
            ],
        }
    }
    sealed = seal_release_evidence({
        "release_ref": "generic-release-v1",
        "runtime_run_id": "run-generic",
        "bundle_sha256": "abc",
        "remediation_status": "VERIFIED",
        "post_action_ref": "artifact://run-generic/post-action-evidence.json",
    })
    result = evaluate_release_gate(
        gate,
        {"recovery_success_rate": 1.0},
        sealed,
    )
    assert result.passed is True
    assert result.decision == "PROMOTE"
