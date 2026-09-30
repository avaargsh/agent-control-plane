from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.factory_attestation import HMACFactoryAttestationVerifier
from agent_control_plane.frozen_evidence import FrozenEvidence
from agent_control_plane.release_evidence import verify_release_evidence

from gpu_xid_golden_incident import plan, registries


def load_json(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def validate_envelope(envelope: dict) -> None:
    identity = envelope.get("identity")
    refs = envelope.get("refs")
    if not isinstance(envelope.get("incidentId"), str) or not envelope["incidentId"]:
        raise ValueError("integration envelope requires incidentId")
    if not isinstance(identity, dict):
        raise ValueError("integration envelope requires identity")
    if not isinstance(refs, dict):
        raise ValueError("integration envelope requires refs")

    for field in (
        "agentReleaseId",
        "sessionId",
        "runId",
        "temporalWorkflowId",
        "temporalRunId",
        "sandbox",
        "computeWorkload",
    ):
        if field not in identity:
            raise ValueError(f"integration envelope identity requires {field}")

    for field in (
        "approvalReceipt",
        "policyDigest",
        "mcpDiagnosticReceipt",
    ):
        if field not in refs:
            raise ValueError(f"integration envelope refs requires {field}")


def run_external(
    *,
    decision_eval: dict,
    factory_artifact: dict,
    factory_attestation: dict,
    integration_envelope: dict,
    factory_key_id: str,
    factory_secret: bytes,
) -> dict:
    validate_envelope(integration_envelope)

    frozen_payload = {
        **integration_envelope,
        "fixtureMode": "external-artifact-integration",
        "providerExecutionMode": "in-memory-control-plane-contract",
    }
    approved = FrozenEvidence.capture(frozen_payload)

    providers, executors = registries()
    result = ApplyReconciler(
        providers=providers,
        executors=executors,
        factory_attestation_verifier=HMACFactoryAttestationVerifier({
            factory_key_id: factory_secret,
        }),
    ).reconcile(
        plan(),
        approved_evidence=approved,
        decision_eval_artifact=decision_eval,
        factory_acceptance_artifact=factory_artifact,
        factory_acceptance_attestation=factory_attestation,
        eval_gates=[{
            "spec": {
                "conditions": [
                    {"metric": "remediation_success", "op": "eq", "value": 1.0},
                    {"metric": "fallback_measured", "op": "eq", "value": 1.0},
                    {"metric": "system2_accuracy", "op": "gte", "value": 0.0},
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

    summary = {
        "fixtureMode": result.evidence["golden_slice"]["fixtureMode"],
        "providerExecutionMode": result.evidence["golden_slice"][
            "providerExecutionMode"
        ],
        "incidentId": result.evidence["golden_slice"]["incidentId"],
        "identity": result.evidence["golden_slice"]["identity"],
        "phase": result.phase,
        "decisionArtifactId": result.evidence["decision_eval"]["artifact_id"],
        "decisionDatasetDigest": result.evidence["decision_eval"]["dataset"]["sha256"],
        "fallbackEvaluation": result.evidence["decision_eval"]["fallback_evaluation"],
        "factoryAcceptanceDigest": (
            result.evidence["factory_acceptance"]["artifact_digest"]
        ),
        "factoryAttestationKeyId": (
            result.evidence["factory_acceptance"]["attestation_key_id"]
        ),
        "factoryTrusted": result.evidence["factory_acceptance"]["trusted"],
        "releaseEvidenceDigest": result.evidence["replay_digest"],
        "replayVerified": verify_release_evidence(result.evidence),
        "releaseEvidence": result.evidence,
    }
    if result.phase != "promoted":
        raise RuntimeError(
            "external artifact integration did not promote: "
            + str(result.error or result.phase)
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-eval", required=True)
    parser.add_argument("--factory-artifact", required=True)
    parser.add_argument("--factory-attestation", required=True)
    parser.add_argument("--integration-envelope", required=True)
    parser.add_argument("--factory-key-id", required=True)
    parser.add_argument(
        "--factory-secret-env",
        default="AI_FACTORY_ATTESTATION_SECRET",
    )
    parser.add_argument("--output")
    args = parser.parse_args()

    secret = os.environ.get(args.factory_secret_env)
    if not secret:
        raise SystemExit(
            "required factory attestation secret environment variable is not set: "
            + args.factory_secret_env
        )

    summary = run_external(
        decision_eval=load_json(args.decision_eval),
        factory_artifact=load_json(args.factory_artifact),
        factory_attestation=load_json(args.factory_attestation),
        integration_envelope=load_json(args.integration_envelope),
        factory_key_id=args.factory_key_id,
        factory_secret=secret.encode("utf-8"),
    )
    rendered = json.dumps(summary, indent=2, ensure_ascii=False)

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")

    print(rendered)


if __name__ == "__main__":
    main()
