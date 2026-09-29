import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/golden-slice-envelope.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def load(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text())


def test_golden_slice_envelope_accepts_canonical_identity_chain() -> None:
    VALIDATOR.validate(load("golden-slice-envelope.valid.json"))


def test_golden_slice_envelope_rejects_mutable_digest_and_unknown_phase() -> None:
    with pytest.raises(ValidationError):
        VALIDATOR.validate(load("golden-slice-envelope.invalid.json"))


def test_run_identity_does_not_depend_on_replaceable_sandbox_identity() -> None:
    before = load("golden-slice-envelope.valid.json")
    after = dict(before, sandboxId="sandbox-cell-c-003")
    VALIDATOR.validate(after)
    assert after["runId"] == before["runId"]
    assert after["workflowId"] == before["workflowId"]
