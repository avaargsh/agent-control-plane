from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .compiler import compile_release_plan
from .eval_engine import evaluate_gate
from .loader import load_yaml_documents


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

    args = parser.parse_args()

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

    if args.command == "gate":
        gates = documents.by_kind(
            "EvalGate"
        )
        if len(gates) != 1:
            raise SystemExit(
                "gate requires exactly one "
                "EvalGate document"
            )

        result = evaluate_gate(
            gates[0],
            _load_metric_map(
                args.metrics
            ),
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

    if len(releases) != 1:
        raise SystemExit(
            "plan requires exactly one "
            "AgentRelease document"
        )

    resolved = compile_release_plan(
        release=releases[0],
        bundles=bundles,
        bindings=bindings,
    )
    print(
        json.dumps(
            asdict(resolved),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
