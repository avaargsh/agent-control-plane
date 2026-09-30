from __future__ import annotations

import hashlib
import hmac
import json

from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.decision_adapter import DecisionGatewayAdapter
from agent_control_plane.example_adapters import (
    CodexHarnessAdapter,
    KubernetesSandboxAdapter,
    TemporalWorkflowAdapter,
)
from agent_control_plane.executors import ExecutorRegistry, InMemoryExecutor
from agent_control_plane.factory_attestation import HMACFactoryAttestationVerifier
from agent_control_plane.frozen_evidence import FrozenEvidence
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.registry import ProviderRegistry
from agent_control_plane.release_evidence import verify_release_evidence
from agent_control_plane.tool_adapter import MCPToolAdapter


FACTORY_KEY_ID = "commissioning-lab"
FACTORY_SECRET = b"synthetic-fixture-secret"


def canonical_digest(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def plan() -> ResolvedReleasePlan:
    chain = [
        ("sandbox", "sandbox", "k8s-agent-sandbox", []),
        ("tools", "tool", "mcp", ["sandbox"]),
        ("harness", "harness", "codex", ["tools"]),
        ("workflow", "workflow", "temporal", ["harness"]),
        ("decision", "decision", "decision-gateway", ["workflow"]),
    ]
    return ResolvedReleasePlan(
        release_name="gpu-xid-remediation-v1",
        bundle_name="sre-gpu-xid",
        version="v1",
        placement={"target": "gpu-cell-a"},
        bindings={
            name: {
                "metadata": {"name": name},
                "spec": {
                    "type": provider_type,
                    "provider": provider,
                    **({"dependsOn": deps} if deps else {}),
                },
            }
            for name, provider_type, provider, deps in chain
        },
    )


def registries() -> tuple[ProviderRegistry, ExecutorRegistry]:
    providers = ProviderRegistry()
    providers.register(KubernetesSandboxAdapter())
    providers.register(MCPToolAdapter())
    providers.register(CodexHarnessAdapter())
    providers.register(TemporalWorkflowAdapter())
    providers.register(DecisionGatewayAdapter())

    executors = ExecutorRegistry()
    for provider_type, provider in [
        ("sandbox", "k8s-agent-sandbox"),
        ("tool", "mcp"),
        ("harness", "codex"),
        ("workflow", "temporal"),
        ("decision", "decision-gateway"),
    ]:
        executors.register(InMemoryExecutor(provider_type, provider))
    return providers, executors


def synthetic_decision_eval_artifact() -> dict:
    payload = {
        "schema_version": "decision-eval/v1",
        "decision_type": "mcp_tool_router",
        "adapter": "candidate-logits",
        "model_ref": "synthetic://Qwen/Qwen3-0.6B-contract-fixture",
        "dataset": {
            "sha256": "sha256:" + "a" * 64,
            "case_count": 2,
            "case_ids": ["gpu-xid-diagnose", "gpu-xid-runbook"],
        },
        "calibration_sha256": "sha256:" + "b" * 64,
        "metrics": {
            "accuracy": 1.0,
            "macro_f1": 1.0,
            "nll": 0.1,
            "brier": 0.02,
            "ece": 0.01,
            "mean_latency_ms": 8.0,
            "p50_latency_ms": 7.0,
            "p95_latency_ms": 10.0,
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
            "adapter": "synthetic-structured-output-fixture",
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
                    "case_id": "gpu-xid-runbook",
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


def synthetic_factory_artifact() -> dict:
    payload = {
        "apiVersion": "aifactory.engineering/v1alpha1",
        "kind": "AcceptanceArtifact",
        "caseId": "gpu-xid-post-recovery-contract-fixture",
        "issuedAt": "2026-09-30T10:00:00+00:00",
        "disposition": "ACCEPT",
        "accepted": True,
        "gates": [
            {"gateId": "compute", "status": "PASS", "reasons": []},
            {"gateId": "fabric", "status": "PASS", "reasons": []},
            {"gateId": "runtime", "status": "PASS", "reasons": []},
        ],
        "reasons": [],
        "evidenceRefs": {
            "compute": "synthetic://gpu-compute/workload/gpu-xid-validation/generation/7",
            "fabric": "synthetic://ai-factory/nccl/gpu-xid-validation",
            "runtime": "synthetic://ai-factory/inference-slo/gpu-xid-validation",
        },
    }
    return {
        **payload,
        "digest": canonical_digest(payload),
    }


def synthetic_factory_attestation(artifact: dict) -> dict:
    unsigned = {
        "apiVersion": "aifactory.engineering/v1alpha1",
        "kind": "AcceptanceAttestation",
        "artifactDigest": artifact["digest"],
        "keyId": FACTORY_KEY_ID,
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
            FACTORY_SECRET,
            message,
            hashlib.sha256,
        ).hexdigest(),
    }


def run_fixture() -> dict:
    alert_evidence = {
        "fixtureMode": "synthetic-contract-fixture",
        "incidentId": "inc-gpu-xid-001",
        "alert": "NVIDIA XID 79",
        "node": "gpu-worker-07",
        "severity": "critical",
        "decision": {
            "decisionId": "decision-gpu-xid-001",
            "action": "cordon-and-recover",
            "requiresApproval": True,
        },
        "identity": {
            "agentReleaseId": "gpu-xid-remediation-v1",
            "sessionId": "session-gpu-xid-001",
            "runId": "run-gpu-xid-001",
            "temporalWorkflowId": "agent-run/run-gpu-xid-001",
            "temporalRunId": "temporal-run-gpu-xid-001",
            "sandbox": {
                "provider": "k8s-agent-sandbox",
                "currentId": "sandbox-b",
                "replacementLineage": ["sandbox-a", "sandbox-b"],
            },
            "computeWorkload": {
                "id": "gpu-xid-validation",
                "generation": 7,
            },
        },
        "refs": {
            "alertEvidence": "synthetic://evidence/gpu-xid/inc-gpu-xid-001",
            "approvalReceipt": "synthetic://approval/inc-gpu-xid-001",
            "policyDigest": "sha256:" + "c" * 64,
            "mcpDiagnosticReceipt": "synthetic://mcp/logs.search/receipt-001",
        },
    }
    approved = FrozenEvidence.capture(alert_evidence)

    # The live source changes while approval is pending. Resume must not consume it.
    alert_evidence["severity"] = "warning"
    alert_evidence["refs"]["alertEvidence"] = "synthetic://live/changed"

    decision_artifact = synthetic_decision_eval_artifact()
    factory_artifact = synthetic_factory_artifact()
    factory_attestation = synthetic_factory_attestation(factory_artifact)

    providers, executors = registries()
    result = ApplyReconciler(
        providers=providers,
        executors=executors,
        factory_attestation_verifier=HMACFactoryAttestationVerifier({
            FACTORY_KEY_ID: FACTORY_SECRET,
        }),
    ).reconcile(
        plan(),
        approved_evidence=approved,
        decision_eval_artifact=decision_artifact,
        factory_acceptance_artifact=factory_artifact,
        factory_acceptance_attestation=factory_attestation,
        eval_gates=[{
            "spec": {
                "conditions": [
                    {"metric": "remediation_success", "op": "eq", "value": 1.0},
                    {"metric": "false_automation_rate", "op": "lte", "value": 0.01},
                    {"metric": "fallback_measured", "op": "eq", "value": 1.0},
                    {"metric": "system2_accuracy", "op": "gte", "value": 0.9},
                    {"metric": "factory_acceptance_trusted", "op": "eq", "value": 1.0},
                    {"metric": "factory_accepted", "op": "eq", "value": 1.0},
                ],
                "onFailure": "rollback",
            }
        }],
        metrics={
            "remediation_success": 1.0,
        },
    )

    return {
        "fixtureMode": result.evidence["golden_slice"]["fixtureMode"],
        "incident": result.evidence["golden_slice"]["incidentId"],
        "approvedSeverity": result.evidence["golden_slice"]["severity"],
        "identity": result.evidence["golden_slice"]["identity"],
        "phase": result.phase,
        "executionOrder": [r.binding_name for r in result.receipts],
        "decisionArtifactId": result.evidence["decision_eval"]["artifact_id"],
        "decisionDatasetDigest": result.evidence["decision_eval"]["dataset"]["sha256"],
        "system2FallbackMeasured": (
            result.evidence["decision_eval"]["fallback_evaluation"]["measured"]
        ),
        "factoryAcceptanceDigest": (
            result.evidence["factory_acceptance"]["artifact_digest"]
        ),
        "factoryAttestationKeyId": (
            result.evidence["factory_acceptance"]["attestation_key_id"]
        ),
        "factoryTrusted": result.evidence["factory_acceptance"]["trusted"],
        "mcpReceiptRef": result.evidence["golden_slice"]["refs"][
            "mcpDiagnosticReceipt"
        ],
        "computeWorkload": result.evidence["golden_slice"]["identity"][
            "computeWorkload"
        ],
        "releaseEvidenceDigest": result.evidence["replay_digest"],
        "replayVerified": verify_release_evidence(result.evidence),
    }


def main() -> None:
    print(
        json.dumps(
            run_fixture(),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
