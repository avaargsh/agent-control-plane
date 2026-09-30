import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[1]
QWEN = (
    ROOT
    / "examples"
    / "evidence"
    / "qwen-mcp-router-2026-09-30.decision-eval.json"
)


def _digest(payload):
    return "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _factory(secret):
    payload = {
        "apiVersion": "aifactory.engineering/v1alpha1",
        "kind": "AcceptanceArtifact",
        "caseId": "controlled-lab-live-shaped",
        "issuedAt": "2026-09-30T10:00:00+00:00",
        "disposition": "ACCEPT",
        "accepted": True,
        "gates": [
            {"gateId": "compute", "status": "PASS", "reasons": []},
            {"gateId": "runtime", "status": "PASS", "reasons": []},
        ],
        "reasons": [],
        "evidenceRefs": {
            "compute": "evidence://gpu/dcgm-live-001",
            "runtime": "evidence://runtime/slo-live-001",
        },
    }
    artifact = {**payload, "digest": _digest(payload)}
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
    return artifact, {**unsigned, "signature": signature}


def _golden_slice():
    return {
        "fixtureMode": "live",
        "incidentId": "inc-live-regression-001",
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


def test_live_runner_preserves_blocked_production_gate_result(tmp_path):
    secret = "live-regression-secret"
    factory, attestation = _factory(secret)

    factory_path = tmp_path / "acceptance.json"
    attestation_path = tmp_path / "attestation.json"
    evidence_path = tmp_path / "golden-slice.json"
    output_path = tmp_path / "release-evidence.json"
    factory_path.write_text(json.dumps(factory), encoding="utf-8")
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    evidence_path.write_text(json.dumps(_golden_slice()), encoding="utf-8")

    env = os.environ.copy()
    env["AI_FACTORY_ATTESTATION_SECRET"] = secret
    completed = subprocess.run(
        [
            sys.executable,
            "examples/run_live_gpu_xid_proof.py",
            "--decision-artifact",
            str(QWEN),
            "--factory-artifact",
            str(factory_path),
            "--factory-attestation",
            str(attestation_path),
            "--golden-slice-evidence",
            str(evidence_path),
            "--factory-key-id",
            "commissioning-lab",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(completed.stdout)
    evidence = json.loads(output_path.read_text(encoding="utf-8"))

    assert summary["phase"] == "blocked"
    assert summary["gatePassed"] is False
    assert summary["replayVerified"] is True
    assert evidence["phase"] == "blocked"
    failed_metrics = {
        violation["metric"]
        for result in evidence["eval_results"]
        for violation in result["violations"]
    }
    assert "system2_accuracy" in failed_metrics
    assert "system2_p95_latency_ms" in failed_metrics
