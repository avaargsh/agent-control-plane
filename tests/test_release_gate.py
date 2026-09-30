from agent_control_plane.release_evidence import seal_release_evidence
from agent_control_plane.release_gate import evaluate_release_gate


GATE = {
    "apiVersion": "agentplane.io/v1alpha1",
    "kind": "EvalGate",
    "metadata": {"name": "golden-authority-gate"},
    "spec": {
        "suiteRef": "golden-remediation",
        "evidenceRequired": True,
        "requiredProvenance": [
            "operation",
            "authority",
        ],
        "onFailure": "block",
        "conditions": [
            {
                "metric": "recovery_success_rate",
                "op": "gte",
                "value": 1.0,
            },
            {
                "metric": "false_automation_rate",
                "op": "eq",
                "value": 0.0,
            },
        ],
    },
}


def evidence():
    return seal_release_evidence({
        "release_ref": "checkout-sre-golden-v1",
        "runtime_run_id": "run-001",
        "bundle_sha256": "abc",
        "remediation_status": "VERIFIED",
        "post_action_ref": (
            "artifact://run-001/"
            "post-action-evidence.json"
        ),
        "operation_id": "op-001",
        "operation_phase": "VERIFIED",
        "operation_evidence_refs": [
            "k8s://ready/4",
            "prometheus://latency/0.18",
        ],
        "operation_approval_id": "approval-001",
        "authority_digest": (
            "sha256:" + "a" * 64
        ),
    })


def evaluate(payload):
    return evaluate_release_gate(
        GATE,
        {
            "recovery_success_rate": 1.0,
            "false_automation_rate": 0.0,
        },
        payload,
    )


def test_promote_requires_valid_evidence_and_passing_eval():
    result = evaluate(evidence())
    assert result.passed is True
    assert result.decision == "PROMOTE"
    assert (
        result.release_ref
        == "checkout-sre-golden-v1"
    )


def test_mutated_evidence_blocks_promotion():
    sealed = evidence()
    sealed["remediation_status"] = "ROLLED_BACK"
    result = evaluate(sealed)
    assert result.passed is False
    assert result.decision == "BLOCK"
    assert (
        result.reason
        == "INVALID_RELEASE_EVIDENCE"
    )


def test_failed_eval_blocks_even_with_valid_evidence():
    result = evaluate_release_gate(
        GATE,
        {
            "recovery_success_rate": 0.0,
            "false_automation_rate": 0.0,
        },
        evidence(),
    )
    assert result.passed is False
    assert result.decision == "BLOCK"
    assert result.reason == "EVAL_GATE_FAILED"


def test_missing_operation_identity_blocks_promotion():
    payload = dict(evidence())
    payload.pop("replay_digest")
    payload.pop("operation_id")
    result = evaluate(
        seal_release_evidence(payload)
    )
    assert result.reason == "MISSING_OPERATION_ID"


def test_non_verified_operation_blocks_promotion():
    payload = dict(evidence())
    payload.pop("replay_digest")
    payload["operation_phase"] = (
        "VERIFICATION_FAILED"
    )
    result = evaluate(
        seal_release_evidence(payload)
    )
    assert result.reason == "OPERATION_NOT_VERIFIED"


def test_missing_operation_evidence_blocks_promotion():
    payload = dict(evidence())
    payload.pop("replay_digest")
    payload["operation_evidence_refs"] = []
    result = evaluate(
        seal_release_evidence(payload)
    )
    assert (
        result.reason
        == "MISSING_OPERATION_EVIDENCE"
    )


def test_expected_authority_digest_mismatch_blocks_promotion():
    result = evaluate_release_gate(
        GATE,
        {
            "recovery_success_rate": 1.0,
            "false_automation_rate": 0.0,
        },
        evidence(),
        expected_authority_digest=(
            "sha256:" + "b" * 64
        ),
    )
    assert (
        result.reason
        == "AUTHORITY_DIGEST_MISMATCH"
    )


def test_invalid_authority_digest_blocks_promotion():
    payload = dict(evidence())
    payload.pop("replay_digest")
    payload["authority_digest"] = (
        "sha256:not-a-digest"
    )
    result = evaluate(
        seal_release_evidence(payload)
    )
    assert (
        result.reason
        == "INVALID_AUTHORITY_DIGEST"
    )


def test_missing_exact_approval_blocks_promotion():
    payload = dict(evidence())
    payload.pop("replay_digest")
    payload["operation_approval_id"] = None
    result = evaluate(
        seal_release_evidence(payload)
    )
    assert (
        result.reason
        == "MISSING_OPERATION_APPROVAL_ID"
    )


def test_generic_evidence_gate_does_not_imply_provenance():
    gate = {
        "apiVersion": "agentplane.io/v1alpha1",
        "kind": "EvalGate",
        "metadata": {
            "name": "generic-evidence-gate"
        },
        "spec": {
            "suiteRef": "generic-smoke",
            "evidenceRequired": True,
            "onFailure": "block",
            "conditions": [
                {
                    "metric": "recovery_success_rate",
                    "op": "gte",
                    "value": 1.0,
                },
            ],
        },
    }
    sealed = seal_release_evidence({
        "release_ref": "generic-release-v1",
        "runtime_run_id": "run-generic",
        "bundle_sha256": "abc",
        "remediation_status": "VERIFIED",
        "post_action_ref": (
            "artifact://run-generic/"
            "post-action-evidence.json"
        ),
    })
    result = evaluate_release_gate(
        gate,
        {"recovery_success_rate": 1.0},
        sealed,
    )
    assert result.passed is True
    assert result.decision == "PROMOTE"
