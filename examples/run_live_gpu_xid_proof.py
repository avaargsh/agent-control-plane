from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

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
from agent_control_plane.live_proof import (
    LiveProofInputs,
    assert_live_release_evidence,
    validate_live_proof_inputs,
)
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.registry import ProviderRegistry
from agent_control_plane.release_evidence import verify_release_evidence
from agent_control_plane.tool_adapter import MCPToolAdapter


def _plan(release_name: str) -> ResolvedReleasePlan:
    chain = [
        ("sandbox", "sandbox", "k8s-agent-sandbox", []),
        ("tools", "tool", "mcp", ["sandbox"]),
        ("harness", "harness", "codex", ["tools"]),
        ("workflow", "workflow", "temporal", ["harness"]),
        ("decision", "decision", "decision-gateway", ["workflow"]),
    ]
    return ResolvedReleasePlan(
        release_name=release_name,
        bundle_name="sre-gpu-xid",
        version="v1",
        placement={"target": "live-gpu-cell"},
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


def _registries():
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-artifact", required=True)
    parser.add_argument("--factory-artifact", required=True)
    parser.add_argument("--factory-attestation", required=True)
    parser.add_argument("--golden-slice-evidence", required=True)
    parser.add_argument("--factory-key-id", required=True)
    parser.add_argument(
        "--factory-secret-env",
        default="AI_FACTORY_ATTESTATION_SECRET",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    secret_value = os.environ.get(args.factory_secret_env)
    if not secret_value:
        raise SystemExit(
            "required factory attestation secret environment "
            f"variable is not set: {args.factory_secret_env}"
        )
    verifier = HMACFactoryAttestationVerifier({
        args.factory_key_id: secret_value.encode("utf-8"),
    })

    decision = json.loads(
        Path(args.decision_artifact).read_text(encoding="utf-8")
    )
    factory = json.loads(
        Path(args.factory_artifact).read_text(encoding="utf-8")
    )
    attestation = json.loads(
        Path(args.factory_attestation).read_text(encoding="utf-8")
    )
    golden_slice = json.loads(
        Path(args.golden_slice_evidence).read_text(encoding="utf-8")
    )

    validate_live_proof_inputs(
        LiveProofInputs(
            decision_artifact=decision,
            factory_artifact=factory,
            factory_attestation=attestation,
        ),
        factory_verifier=verifier,
    )
    assert_live_release_evidence(
        {"golden_slice": golden_slice}
    )

    identity = golden_slice["identity"]
    release_name = identity["agentReleaseId"]
    approved = FrozenEvidence.capture(golden_slice)

    providers, executors = _registries()
    result = ApplyReconciler(
        providers=providers,
        executors=executors,
        factory_attestation_verifier=verifier,
    ).reconcile(
        _plan(release_name),
        approved_evidence=approved,
        decision_eval_artifact=decision,
        factory_acceptance_artifact=factory,
        factory_acceptance_attestation=attestation,
        eval_gates=[{
            "spec": {
                "conditions": [
                    {"metric": "fallback_measured", "op": "eq", "value": 1.0},
                    {"metric": "system2_accuracy", "op": "gte", "value": 0.0},
                    {"metric": "factory_acceptance_trusted", "op": "eq", "value": 1.0},
                    {"metric": "factory_accepted", "op": "eq", "value": 1.0},
                ],
                "onFailure": "block",
            }
        }],
    )

    if result.phase != "promoted":
        raise SystemExit(
            "live release did not promote: "
            + str(result.error)
        )
    if not verify_release_evidence(result.evidence):
        raise SystemExit("final ReleaseEvidence replay verification failed")
    assert_live_release_evidence(result.evidence)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result.evidence, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "phase": result.phase,
                "release": release_name,
                "decisionArtifactId": result.evidence[
                    "decision_eval"
                ]["artifact_id"],
                "factoryAcceptanceDigest": result.evidence[
                    "factory_acceptance"
                ]["artifact_digest"],
                "releaseEvidenceDigest": result.evidence[
                    "replay_digest"
                ],
                "replayVerified": True,
                "output": str(output),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
