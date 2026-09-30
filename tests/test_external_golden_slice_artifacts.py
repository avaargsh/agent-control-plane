import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys


def canonical_digest(payload):
    return "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def decision_artifact():
    payload = {
        "schema_version": "decision-eval/v1",
        "decision_type": "mcp_tool_router",
        "adapter": "candidate-logits",
        "model_ref": "Qwen/Qwen3-0.6B",
        "dataset": {
            "sha256": "sha256:" + "a" * 64,
            "case_count": 2,
            "case_ids": ["a", "b"],
        },
        "calibration_sha256": "sha256:" + "b" * 64,
        "metrics": {
            "accuracy": 0.9,
            "macro_f1": 0.9,
            "nll": 0.2,
            "brier": 0.1,
            "ece": 0.05,
            "mean_latency_ms": 10.0,
            "p50_latency_ms": 8.0,
            "p95_latency_ms": 14.0,
            "mean_tokens_processed_per_decision": 24.0,
        },
        "operating_point": {
            "threshold": 0.8,
            "coverage": 0.5,
            "risk": 0.0,
            "false_automation_rate": 0.0,
            "fallback_rate": 0.5,
            "risk_budget": 0.0,
        },
        "fallback_evaluation": {
            "measured": True,
            "adapter": "transformers-structured-output",
            "threshold": 0.8,
            "eligible_case_count": 2,
            "fallback_case_count": 1,
            "fallback_rate": 0.5,
            "accuracy": 1.0,
            "p50_latency_ms": 20.0,
            "p95_latency_ms": 20.0,
            "mean_tokens_processed": 48.0,
            "cases": [
                {
                    "case_id": "b",
                    "fast_confidence": 0.55,
                    "fast_predicted": "logs.search",
                    "fallback_predicted": "runbook.search",
                    "gold": "runbook.search",
                    "correct": True,
                    "latency_ms": 20.0,
                    "tokens_processed": 48,
                }
            ],
        },
    }
    digest = canonical_digest(payload)
    return {
        **payload,
        "artifact_id": "decision-eval:" + digest,
        "content_digest": digest,
    }


def factory_artifact():
    payload = {
        "apiVersion": "aifactory.engineering/v1alpha1",
        "kind": "AcceptanceArtifact",
        "caseId": "external-test",
        "issuedAt": "2026-09-30T10:00:00+00:00",
        "disposition": "ACCEPT",
        "accepted": True,
        "gates": [
            {"gateId": "compute", "status": "PASS", "reasons": []},
        ],
        "reasons": [],
        "evidenceRefs": {
            "compute": "external://compute/evidence-001",
        },
    }
    return {
        **payload,
        "digest": canonical_digest(payload),
    }


def factory_attestation(artifact, secret):
    unsigned = {
        "apiVersion": "aifactory.engineering/v1alpha1",
        "kind": "AcceptanceAttestation",
        "artifactDigest": artifact["digest"],
        "keyId": "commissioning-lab",
        "algorithm": "HMAC-SHA256",
    }
    signature = hmac.new(
        secret.encode("utf-8"),
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {**unsigned, "signature": signature}


def test_external_artifacts_enter_same_release_evidence_path(tmp_path):
    root = Path(__file__).parents[1]
    secret = "external-test-secret"
    factory = factory_artifact()

    decision_path = tmp_path / "decision-eval.json"
    factory_path = tmp_path / "acceptance.json"
    attestation_path = tmp_path / "attestation.json"
    envelope_path = root / "examples" / "gpu-xid-external-envelope.example.json"

    decision_path.write_text(json.dumps(decision_artifact()), encoding="utf-8")
    factory_path.write_text(json.dumps(factory), encoding="utf-8")
    attestation_path.write_text(
        json.dumps(factory_attestation(factory, secret)),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["AI_FACTORY_ATTESTATION_SECRET"] = secret
    completed = subprocess.run(
        [
            sys.executable,
            "examples/gpu_xid_external_artifacts.py",
            "--decision-eval",
            str(decision_path),
            "--factory-artifact",
            str(factory_path),
            "--factory-attestation",
            str(attestation_path),
            "--integration-envelope",
            str(envelope_path),
            "--factory-key-id",
            "commissioning-lab",
        ],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    output = json.loads(completed.stdout)
    assert output["fixtureMode"] == "external-artifact-integration"
    assert output["providerExecutionMode"] == "in-memory-control-plane-contract"
    assert output["phase"] == "promoted"
    assert output["replayVerified"] is True
    assert output["factoryTrusted"] is True
    assert output["decisionArtifactId"].startswith("decision-eval:sha256:")
