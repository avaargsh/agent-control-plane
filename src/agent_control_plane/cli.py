from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .compiler import compile_release_plan
from .loader import load_yaml_documents


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-control-plane")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("path")

    plan = subparsers.add_parser("plan")
    plan.add_argument("path")

    args = parser.parse_args()

    documents = load_yaml_documents(args.path, validate=True)

    if args.command == "validate":
        print(f"validated {len(documents.documents)} document(s)")
        return

    bundles = documents.by_kind("AgentBundle")
    releases = documents.by_kind("AgentRelease")
    bindings = documents.by_kind("RuntimeBinding")

    if len(releases) != 1:
        raise SystemExit("plan requires exactly one AgentRelease document")

    resolved = compile_release_plan(
        release=releases[0],
        bundles=bundles,
        bindings=bindings,
    )
    print(json.dumps(asdict(resolved), indent=2))


if __name__ == "__main__":
    main()
