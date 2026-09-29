import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agent_control_plane.golden_slice import validate_golden_slice_transition

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/golden-slice-envelope.schema.json").read_text())
TRAJECTORY = json.loads((ROOT / "examples/golden-slice-trajectory.json").read_text())


def test_golden_slice_trajectory_is_schema_valid_and_replayable():
    validator = Draft202012Validator(SCHEMA)
    for envelope in TRAJECTORY:
        validator.validate(envelope)
    for before, after in zip(TRAJECTORY, TRAJECTORY[1:], strict=True):
        validate_golden_slice_transition(before, after)


def test_trajectory_preserves_canonical_identity_across_sandbox_replacement():
    executing = next(item for item in TRAJECTORY if item["phase"] == "executing")
    verifying = next(item for item in TRAJECTORY if item["phase"] == "verifying")
    assert executing["sandboxId"] != verifying["sandboxId"]
    for field in ("runId", "evidenceId", "evidenceDigest", "decisionId", "releaseId", "workflowId"):
        assert executing[field] == verifying[field]


def test_trajectory_ends_in_terminal_release_outcome():
    assert TRAJECTORY[-1]["phase"] in {"promoted", "blocked", "rolled_back"}
