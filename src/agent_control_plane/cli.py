from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from .authority import (
    admit_authority_change,
    build_authority_inventory,
    decision_asdict,
)
from .compiler import compile_release_plan
from .decision_eval import validate_decision_eval_artifact
from .eval_engine import evaluate_gate
from .factory_attestation import HMACFactoryAttestationVerifier
from .live_proof import LiveProofInputs, validate_live_proof_inputs
from .loader import load_yaml_documents
from .release_gate import evaluate_release_gate


def _load_metric_map(
    path: str,
) -> dict[str, float]:
    payload = json.loads(
        Path(path).read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(payload, dict):
        raise ValueError(
            "metrics JSON must be an object"
        )

    return {
        str(key): float(value)
        for key, value in payload.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agent-control-plane"
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    validate = subparsers.add_parser(
        "validate"
    )
    validate.add_argument("path")

    plan = subparsers.add_parser("plan")
    plan.add_argument("path")

    gate = subparsers.add_parser("gate")
    gate.add_argument("path")
    gate.add_argument(
        "--metrics",
        required=True,
    )
    gate.add_argument(
        "--evidence",
        required=False,
    )
    gate.add_argument(
        "--authority-digest",
        required=False,
    )

    inventory = subparsers.add_parser(
        "authority-inventory"
    )
    inventory.add_argument("path")

    authority_admit = subparsers.add_parser(
        "authority-admit"
    )
    authority_admit.add_argument("path")
    authority_admit.add_argument(
        "--baseline",
        required=False,
    )
    authority_admit.add_argument(
        "--allow-initial",
        action="store_true",
    )

    decision_verify = subparsers.add_parser(
        "decision-eval-verify"
    )
    decision_verify.add_argument("artifact")

    decision_gate = subparsers.add_parser(
        "decision-eval-gate"
    )
    decision_gate.add_argument("path")
    decision_gate.add_argument(
        "--artifact",
        required=True,
    )

    live_proof = subparsers.add_parser(
        "live-proof-verify"
    )
    live_proof.add_argument(
        "--decision-artifact",
        required=True,
    )
    live_proof.add_argument(
        "--factory-artifact",
        required=True,
    )
    live_proof.add_argument(
        "--factory-attestation",
        required=True,
    )
    live_proof.add_argument(
        "--factory-key-id",
        required=True,
    )
    live_proof.add_argument(
        "--factory-secret-env",
        default="AI_FACTORY_ATTESTATION_SECRET",
    )

    args = parser.parse_args()

    if args.command == "live-proof-verify":
        secret_value = os.environ.get(
            args.factory_secret_env
        )
        if not secret_value:
            raise SystemExit(
                "required factory attestation secret "
                f"environment variable is not set: "
                f"{args.factory_secret_env}"
            )
        inputs = LiveProofInputs(
            decision_artifact=json.loads(
                Path(args.decision_artifact).read_text(
                    encoding="utf-8"
                )
            ),
            factory_artifact=json.loads(
                Path(args.factory_artifact).read_text(
                    encoding="utf-8"
                )
            ),
            factory_attestation=json.loads(
                Path(args.factory_attestation).read_text(
                    encoding="utf-8"
                )
            ),
        )
        try:
            summary = validate_live_proof_inputs(
                inputs,
                factory_verifier=(
                    HMACFactoryAttestationVerifier({
                        args.factory_key_id: (
                            secret_value.encode("utf-8")
                        ),
                    })
                ),
            )
        except ValueError as exc:
            print(
                json.dumps(
                    {
                        "valid": False,
                        "reason": str(exc),
                    },
                    indent=2,
                )
            )
            raise SystemExit(3)
        print(
            json.dumps(
                {
                    "valid": True,
                    **summary,
                },
                indent=2,
            )
        )
        return

    if args.command in {
        "decision-eval-verify",
        "decision-eval-gate",
    }:
        artifact = json.loads(
            Path(args.artifact).read_text(
                encoding="utf-8"
            )
        )
        validation = validate_decision_eval_artifact(
            artifact
        )
        if not validation.valid:
            print(
                json.dumps(
                    {
                        "valid": False,
                        "reason": validation.reason,
                    },
                    indent=2,
                )
            )
            raise SystemExit(3)

        if args.command == "decision-eval-verify":
            print(
                json.dumps(
                    {
                        "valid": True,
                        "artifact_id": validation.artifact_id,
                        "content_digest": validation.content_digest,
                        "metrics": dict(validation.metrics),
                    },
                    indent=2,
                )
            )
            return

        documents = load_yaml_documents(
            args.path,
            validate=True,
        )
        gates = documents.by_kind("EvalGate")
        if len(gates) != 1:
            raise SystemExit(
                "decision-eval-gate requires exactly one "
                "EvalGate document"
            )
        result = evaluate_gate(
            gates[0],
            validation.metrics,
        )
        print(
            json.dumps(
                {
                    "artifact_id": validation.artifact_id,
                    "content_digest": validation.content_digest,
                    "gate": asdict(result),
                },
                indent=2,
            )
        )
        raise SystemExit(
            0 if result.passed else 2
        )

    documents = load_yaml_documents(
        args.path,
        validate=True,
    )

    if args.command == "validate":
        print(
            f"validated "
            f"{len(documents.documents)} "
            "document(s)"
        )
        return

    if args.command == "authority-inventory":
        envelopes = documents.by_kind(
            "AgentAuthorityEnvelope"
        )
        print(
            json.dumps(
                build_authority_inventory(envelopes),
                indent=2,
            )
        )
        return

    if args.command == "authority-admit":
        proposed = documents.by_kind(
            "AgentAuthorityEnvelope"
        )
        if len(proposed) != 1:
            raise SystemExit(
                "authority-admit requires exactly one "
                "AgentAuthorityEnvelope document"
            )

        baseline = None
        if args.baseline:
            baseline_docs = load_yaml_documents(
                args.baseline,
                validate=True,
            ).by_kind("AgentAuthorityEnvelope")
            if len(baseline_docs) != 1:
                raise SystemExit(
                    "--baseline requires exactly one "
                    "AgentAuthorityEnvelope document"
                )
            baseline = baseline_docs[0]

        result = admit_authority_change(
            baseline,
            proposed[0],
            allow_initial=args.allow_initial,
        )
        print(
            json.dumps(
                decision_asdict(result),
                indent=2,
            )
        )
        raise SystemExit(
            0 if result.admitted else 3
        )

    if args.command == "gate":
        gates = documents.by_kind(
            "EvalGate"
        )
        if len(gates) != 1:
            raise SystemExit(
                "gate requires exactly one "
                "EvalGate document"
            )

        metrics = _load_metric_map(args.metrics)
        if args.evidence:
            evidence = json.loads(
                Path(args.evidence).read_text(encoding="utf-8")
            )
            result = evaluate_release_gate(
                gates[0],
                metrics,
                evidence,
                expected_authority_digest=(
                    args.authority_digest
                ),
            )
        else:
            result = evaluate_gate(
                gates[0],
                metrics,
            )
        print(
            json.dumps(
                asdict(result),
                indent=2,
            )
        )
        raise SystemExit(
            0 if result.passed else 2
        )

    bundles = documents.by_kind(
        "AgentBundle"
    )
    releases = documents.by_kind(
        "AgentRelease"
    )
    bindings = documents.by_kind(
        "RuntimeBinding"
    )
    capability_intents = documents.by_kind(
        "CapabilityIntent"
    )
    tool_contracts = documents.by_kind(
        "ToolContract"
    )

    if len(releases) != 1:
        raise SystemExit(
            "plan requires exactly one "
            "AgentRelease document"
        )

    resolved = compile_release_plan(
        release=releases[0],
        bundles=bundles,
        bindings=bindings,
        capability_intents=capability_intents,
        tool_contracts=tool_contracts,
    )
    print(
        json.dumps(
            asdict(resolved),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
