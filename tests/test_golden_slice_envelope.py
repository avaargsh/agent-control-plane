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


def test_observed_phase_requires_only_canonical_run_identity() -> None:
    VALIDATOR.validate({
        "apiVersion": "agentinfra.dev/v1alpha1",
        "kind": "GoldenSliceEnvelope",
        "runId": "run-golden-001",
        "phase": "observed",
    })


def test_evidence_frozen_requires_immutable_evidence_identity() -> None:
    with pytest.raises(ValidationError):
        VALIDATOR.validate({
            "apiVersion": "agentinfra.dev/v1alpha1",
            "kind": "GoldenSliceEnvelope",
            "runId": "run-golden-001",
            "phase": "evidence_frozen",
        })


def test_decided_phase_requires_decision_identity() -> None:
    with pytest.raises(ValidationError):
        VALIDATOR.validate({
            "apiVersion": "agentinfra.dev/v1alpha1",
            "kind": "GoldenSliceEnvelope",
            "runId": "run-golden-001",
            "phase": "decided",
            "evidenceId": "evidence-001",
            "evidenceDigest": "sha256:" + "a" * 64,
        })


def test_executing_phase_requires_release_and_workflow_but_not_sandbox() -> None:
    envelope = {
        "apiVersion": "agentinfra.dev/v1alpha1",
        "kind": "GoldenSliceEnvelope",
        "runId": "run-golden-001",
        "phase": "executing",
        "evidenceId": "evidence-001",
        "evidenceDigest": "sha256:" + "a" * 64,
        "decisionId": "decision-sha256:" + "c" * 64,
        "releaseId": "sre-v1",
        "workflowId": "wf-golden-001",
    }
    VALIDATOR.validate(envelope)
    VALIDATOR.validate(dict(envelope, sandboxId="sandbox-replacement-002"))
