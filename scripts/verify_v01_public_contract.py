#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from dataclasses import fields, is_dataclass
from pathlib import Path

import agent_control_plane
from agent_control_plane import authority as authority_module
from agent_control_plane import validator as validator_module
from agent_control_plane.independent_verifier import (
    IndependentExecutionProof,
    _PREDICATE_TYPE,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "release" / "v0.1-public-contract.json"


def fail(message: str) -> None:
    raise SystemExit(f"v0.1 public contract mismatch: {message}")


def field_names(value: type[object]) -> list[str]:
    if not is_dataclass(value):
        fail(f"{value.__name__} is no longer a dataclass")
    return [item.name for item in fields(value)]


def main() -> int:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    if project.get("version") != contract["packageVersion"]:
        fail(
            "package version changed: "
            f"{project.get('version')} != {contract['packageVersion']}"
        )

    scripts = project.get("scripts", {})
    if sorted(scripts) != sorted(contract["consoleScripts"]):
        fail(
            "console script names changed: "
            f"{sorted(scripts)} != {sorted(contract['consoleScripts'])}"
        )

    if list(agent_control_plane.__all__) != contract["topLevelExports"]:
        fail(
            "top-level exports changed: "
            f"{list(agent_control_plane.__all__)}"
        )

    for name, expected in contract["exportedDataclassFields"].items():
        value = getattr(authority_module, name, None)
        if value is None:
            fail(f"exported dataclass disappeared: {name}")
        actual = field_names(value)
        if actual != expected:
            fail(f"{name} fields changed: {actual} != {expected}")

    actual_schemas = dict(validator_module._KIND_TO_SCHEMA)
    if actual_schemas != contract["manifestSchemas"]:
        fail(
            "manifest kind/schema mapping changed: "
            f"{actual_schemas} != {contract['manifestSchemas']}"
        )
    schema_dir = validator_module.schema_directory()
    missing_schemas = [
        filename
        for filename in contract["manifestSchemas"].values()
        if not (schema_dir / filename).is_file()
    ]
    if missing_schemas:
        fail(f"packaged schemas missing: {missing_schemas}")

    proof_contract = contract["independentExecutionProof"]
    if field_names(IndependentExecutionProof) != proof_contract[
        "dataclassFields"
    ]:
        fail("IndependentExecutionProof dataclass fields changed")
    if _PREDICATE_TYPE != proof_contract["predicateType"]:
        fail(
            "IndependentExecutionProof predicate type changed: "
            f"{_PREDICATE_TYPE}"
        )
    default_version = IndependentExecutionProof.__dataclass_fields__[
        "statement_version"
    ].default
    if default_version != proof_contract["statementVersion"]:
        fail(
            "IndependentExecutionProof statement version changed: "
            f"{default_version}"
        )

    completed = subprocess.run(
        [sys.executable, "-m", "agent_control_plane.cli", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    help_text = completed.stdout
    missing_commands = [
        command
        for command in contract["cliCommands"]
        if command not in help_text
    ]
    if missing_commands:
        fail(f"CLI commands missing from --help: {missing_commands}")

    result = {
        "contractVersion": contract["contractVersion"],
        "packageVersion": contract["packageVersion"],
        "consoleScripts": contract["consoleScripts"],
        "cliCommands": contract["cliCommands"],
        "topLevelExports": contract["topLevelExports"],
        "manifestSchemaCount": len(contract["manifestSchemas"]),
        "independentExecutionProof": {
            "predicateType": proof_contract["predicateType"],
            "statementVersion": proof_contract["statementVersion"],
            "fields": proof_contract["dataclassFields"],
        },
        "verified": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
