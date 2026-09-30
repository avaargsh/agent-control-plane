from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class ManifestValidationError(ValueError):
    pass


_KIND_TO_SCHEMA = {
    "AgentBundle": "agent-bundle.schema.json",
    "AgentRelease": "agent-release.schema.json",
    "AgentAuthorityEnvelope": "agent-authority-envelope.schema.json",
    "RuntimeBinding": "runtime-binding.schema.json",
    "Session": "session.schema.json",
    "Run": "run.schema.json",
    "StateRef": "state-ref.schema.json",
    "EvidenceRef": "evidence-ref.schema.json",
    "EvalGate": "eval-gate.schema.json",
    "ContextProjectionPolicy": "context-projection-policy.schema.json",
}


def schema_directory() -> Path:
    packaged = Path(__file__).resolve().parent / "schemas"
    if packaged.is_dir():
        return packaged

    source_checkout = Path(__file__).resolve().parents[2] / "schemas"
    if source_checkout.is_dir():
        return source_checkout

    raise FileNotFoundError(
        "agent-control-plane manifest schemas are not installed"
    )


def validate_manifest(
    document: dict[str, Any],
    *,
    schema_dir: str | Path | None = None,
) -> None:
    kind = document.get("kind")
    if kind not in _KIND_TO_SCHEMA:
        raise ManifestValidationError(f"unsupported kind: {kind!r}")

    directory = Path(schema_dir) if schema_dir is not None else schema_directory()
    schema_path = directory / _KIND_TO_SCHEMA[kind]

    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))

    if errors:
        detail = "; ".join(
            f"{'/'.join(str(x) for x in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ManifestValidationError(detail)
