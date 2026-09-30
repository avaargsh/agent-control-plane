import hashlib
import hmac
import json

import pytest

from agent_control_plane.factory_attestation import (
    HMACFactoryAttestationVerifier,
)
from agent_control_plane.live_proof import (
    LiveProofInputs,
    assert_live_release_evidence,
    validate_live_proof_inputs,
)


SECRET = b"lab-secret"
KEY_ID = "commissioning-lab"


def _digest(payload):
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _decision(*, synthetic=False, measured=True, fallback_count=1):
    payload = {
        "schema_version": "decision-eval/v1",
        "decision_type": "mcp_tool_router",
        "adapter": (
            "synthetic-structured-output-fixture"
            if synthetic
            else "candidate-logits"
        ),
        "model_ref": (
            "synthetic://qwen-contract-fixture"
            if synthetic
            else "Qwen/Qwen3-0.6B"
        ),
        "dataset": {
            "sha256": "sha256:" + "a" * 64,
            "case_count": 2,
            "case_ids": ["case-a", "case-b"],
        },
        "calibration_sha256": "sha256:" + "b" * 64,
        "metrics": {
            "accuracy": 0.95,
            "macro_f1": 0.94,
            "nll": 0.2,
            "brier": 0.08,
            "ece": 0.03,
            "mean_latency_ms": 12.0,
            "p50_latency_ms": 10.0,
            "p95_latency_ms": 18.0,
            "mean_tokens_processed_per_decision": 32.0,
        },
        "operating_point": {
            "threshold": 0.8,
            "coverage": 0.5,
            "risk": 0.0,
            "false_automation_rate": 0.0,
            "fallback_rate": 0.5,
            "risk_budget": 0.0,
        },
        "fallback_evaluation": (
            {
                "measured": True,
                "adapter": "transformers-structured-output",
                "threshold": 0.8,
                "eligible_case_count": 2,
                "fallback_case_count": fallback_count,
                "fallback_rate": fallback_count / 2,
                "accuracy": 1.0 if fallback_count else None,
                "p50_latency_ms": 20.0 if fallback_count else None,
                "p95_latency_ms": 20.0 if fallback_count else None,
                "mean_tokens_processed": 48.0 if fallback_count else None,
                "cases": (
                    [{
                        "case_id": "case-b",
                        "fast_confidence": 0.55,
                        "fast_predicted": "prometheus.query",
                        "fallback_predicted": "logs.search",
                        "gold": "logs.search",
                        "correct": True,
                        "latency_ms": 20.0,
                        "tokens_processed": 48,
                    }]
                    if fallback_count
                    else []
                ),
            }
            if measured
            else {"measured": False}
        ),
    }
    digest = _digest(payload)
    return {
        **payload,
        "artifact_id": "decision-eval:" + digest,
        "content_digest": digest,
    }


def _factory(*, synthetic=False):
    payload = {
        "apiVersion": "aifactory.engineering/v1alpha1",
        "kind": "AcceptanceArtifact",
        "caseId": "controlled-lab-live",
        "issuedAt": "2026-09-30T10:00:00+00:00",
        "disposition": "ACCEPT",
        "accepted": True,
        "gates": [
            {"gateId": "compute", "status": "PASS", "reasons": []},
            {"gateId": "runtime", "status": "PASS", "reasons": []},
        ],
        "reasons": [],
        "evidenceRefs": {
            "compute": (
                "synthetic://gpu/dcgm"
                if synthetic
                else "evidence://gpu/dcgm-live-001"
            ),
            "runtime": "evidence://runtime/slo-live-001",
        },
    }
    return {**payload, "digest": _digest(payload)}


def _attestation(artifact):
    unsigned = {
        "apiVersion": "aifactory.engineering/v1alpha1",
        "kind": "AcceptanceAttestation",
        "artifactDigest": artifact["digest"],
        "keyId": KEY_ID,
        "algorithm": "HMAC-SHA256",
    }
    message = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        **unsigned,
        "signature": hmac.new(
            SECRET,
            message,
            hashlib.sha256,
        ).hexdigest(),
    }


def _verify(decision, factory):
    return validate_live_proof_inputs(
        LiveProofInputs(
            decision_artifact=decision,
            factory_artifact=factory,
            factory_attestation=_attestation(factory),
        ),
        factory_verifier=HMACFactoryAttestationVerifier({
            KEY_ID: SECRET,
        }),
    )


def test_live_shaped_artifacts_pass_contract_verification():
    summary = _verify(_decision(), _factory())

    assert summary["system2_fallback_case_count"] == 1
    assert summary["factory_accepted"] is True
    assert summary["factory_attestation_key_id"] == KEY_ID


def test_synthetic_decision_artifact_is_rejected():
    with pytest.raises(
        ValueError,
        match="DECISION_ARTIFACT_SYNTHETIC",
    ):
        _verify(
            _decision(synthetic=True),
            _factory(),
        )


def test_synthetic_factory_artifact_is_rejected():
    with pytest.raises(
        ValueError,
        match="FACTORY_ARTIFACT_SYNTHETIC",
    ):
        _verify(
            _decision(),
            _factory(synthetic=True),
        )


def test_live_proof_requires_fallback_to_execute():
    with pytest.raises(
        ValueError,
        match="SYSTEM2_FALLBACK_NOT_EXERCISED",
    ):
        _verify(
            _decision(fallback_count=0),
            _factory(),
        )



def _golden_slice():
    return {
        "fixtureMode": "live",
        "incidentId": "inc-live-001",
        "alert": "NVIDIA XID 79",
        "node": "gpu-worker-01",
        "severity": "critical",
        "decision": {
            "decisionId": "decision-live-001",
            "action": "diagnose",
            "requiresApproval": True,
        },
        "identity": {
            "agentReleaseId": "gpu-xid-live-v1",
            "sessionId": "session-live-001",
            "runId": "run-live-001",
            "temporalWorkflowId": "workflow-live-001",
            "temporalRunId": "workflow-run-live-001",
            "sandbox": {
                "provider": "k8s-agent-sandbox",
                "currentId": "sandbox-live-001",
                "replacementLineage": [],
            },
            "computeWorkload": {
                "id": "workload-live-001",
                "generation": 1,
            },
        },
        "refs": {
            "alertEvidence": "evidence://alerts/live-001",
            "approvalReceipt": "evidence://approval/live-001",
            "policyDigest": "sha256:" + "c" * 64,
            "mcpDiagnosticReceipt": "evidence://mcp/live-001",
        },
    }


def test_live_release_evidence_rejects_template_placeholders():
    value = _golden_slice()
    value["identity"]["runId"] = "REPLACE_ME"

    with pytest.raises(
        ValueError,
        match="LIVE_GOLDEN_SLICE_PLACEHOLDER",
    ):
        assert_live_release_evidence(
            {"golden_slice": value}
        )


def test_live_release_evidence_rejects_incomplete_nested_identity():
    value = _golden_slice()
    value["identity"]["sandbox"]["currentId"] = ""

    with pytest.raises(
        ValueError,
        match="LIVE_SANDBOX_IDENTITY_INCOMPLETE:currentId",
    ):
        assert_live_release_evidence(
            {"golden_slice": value}
        )


def test_live_release_evidence_requires_positive_compute_generation():
    value = _golden_slice()
    value["identity"]["computeWorkload"]["generation"] = 0

    with pytest.raises(
        ValueError,
        match="LIVE_COMPUTE_IDENTITY_INCOMPLETE:generation",
    ):
        assert_live_release_evidence(
            {"golden_slice": value}
        )


def test_live_release_evidence_validates_policy_digest():
    value = _golden_slice()
    value["refs"]["policyDigest"] = "sha256:not-a-real-digest"

    with pytest.raises(
        ValueError,
        match="LIVE_POLICY_DIGEST_INVALID",
    ):
        assert_live_release_evidence(
            {"golden_slice": value}
        )
