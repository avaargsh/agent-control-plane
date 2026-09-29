import pytest

from agent_control_plane.golden_slice import validate_golden_slice_transition


def envelope(phase: str, **extra):
    value = {"runId": "run-001", "phase": phase}
    value.update(extra)
    return value


def test_allows_identity_accumulation_across_lifecycle():
    before = envelope("evidence_frozen", evidenceId="e-1", evidenceDigest="sha256:" + "a" * 64)
    after = envelope("decided", evidenceId="e-1", evidenceDigest="sha256:" + "a" * 64, decisionId="decision-sha256:" + "b" * 64)
    validate_golden_slice_transition(before, after)


def test_rejects_evidence_digest_drift():
    before = envelope("evidence_frozen", evidenceId="e-1", evidenceDigest="sha256:" + "a" * 64)
    after = envelope("decided", evidenceId="e-1", evidenceDigest="sha256:" + "c" * 64, decisionId="d-1")
    with pytest.raises(ValueError, match="evidenceDigest"):
        validate_golden_slice_transition(before, after)


def test_rejects_decision_identity_drift_after_decision():
    before = envelope("decided", evidenceId="e-1", evidenceDigest="sha256:" + "a" * 64, decisionId="d-1")
    after = dict(before, phase="executing", decisionId="d-2", releaseId="r-1", workflowId="w-1")
    with pytest.raises(ValueError, match="decisionId"):
        validate_golden_slice_transition(before, after)


def test_allows_sandbox_replacement_without_run_or_workflow_change():
    before = envelope("executing", evidenceId="e-1", evidenceDigest="sha256:" + "a" * 64, decisionId="d-1", releaseId="r-1", workflowId="w-1", sandboxId="s-1")
    after = dict(before, phase="verifying", sandboxId="s-2")
    validate_golden_slice_transition(before, after)


def test_rejects_transition_out_of_terminal_phase():
    before = envelope("promoted", evidenceId="e-1", evidenceDigest="sha256:" + "a" * 64, decisionId="d-1", releaseId="r-1", workflowId="w-1")
    with pytest.raises(ValueError, match="terminal"):
        validate_golden_slice_transition(before, dict(before, phase="promoted"))


def test_rejects_backward_phase_transition():
    before = envelope("verifying", evidenceId="e-1", evidenceDigest="sha256:" + "a" * 64, decisionId="d-1", releaseId="r-1", workflowId="w-1")
    with pytest.raises(ValueError, match="backwards"):
        validate_golden_slice_transition(before, dict(before, phase="executing"))
