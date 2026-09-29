from agent_control_plane.release_evidence import seal_release_evidence
from agent_control_plane.release_gate import evaluate_release_gate


GATE = {
    "spec": {
        "evidenceRequired": True,
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
