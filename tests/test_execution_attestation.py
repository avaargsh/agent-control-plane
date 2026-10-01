from dataclasses import replace
from datetime import timedelta

import pytest

from agent_control_plane.execution_attestation import ExecutionAttestation
from agent_control_plane.execution_journal import (
    ExecutionContextProvenance,
    SQLiteExecutionJournal,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation
from test_execution_context_provenance import _prepare_context_attempt
from test_kubernetes_deployment_transition import NOW, build_transition


def test_attestation_binds_committed_execution_and_context_provenance(
    tmp_path,
):
    (
        _,
        _,
        _,
        journal,
        provenance,
        attempt,
    ) = _prepare_context_attempt(tmp_path)
    committed = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=5),
        result={"status": "APPLIED"},
    )

    attestation = ExecutionAttestation.seal(
        attestation_id="attestation-001",
        attempt=committed,
        outcome_contract_hash="sha256:" + "a" * 64,
        observation_evidence_hash="sha256:" + "b" * 64,
        verification_status="SUCCEEDED",
        verified_at=NOW + timedelta(seconds=6),
    )
    attestation.verify()
    attestation.verify_attempt(committed)

    assert (
        attestation.context_provenance_hash
        == provenance.provenance_hash
    )
    assert attestation.attempt_hash == committed.attempt_hash
    assert attestation.terminal_result_hash == committed.result_hash


def test_attestation_rejects_prepared_attempt(tmp_path):
    _, _, _, _, _, attempt = _prepare_context_attempt(tmp_path)

    with pytest.raises(
        ProtocolViolation,
        match="requires COMMITTED attempt",
    ):
        ExecutionAttestation.seal(
            attestation_id="attestation-prepared",
            attempt=attempt,
            outcome_contract_hash="sha256:" + "a" * 64,
            observation_evidence_hash="sha256:" + "b" * 64,
            verification_status="SUCCEEDED",
            verified_at=NOW + timedelta(seconds=6),
        )


def test_attestation_detects_tampering(tmp_path):
    _, _, _, journal, _, attempt = _prepare_context_attempt(tmp_path)
    committed = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=5),
        result={"status": "APPLIED"},
    )
    attestation = ExecutionAttestation.seal(
        attestation_id="attestation-tamper",
        attempt=committed,
        outcome_contract_hash="sha256:" + "a" * 64,
        observation_evidence_hash="sha256:" + "b" * 64,
        verification_status="SUCCEEDED",
        verified_at=NOW + timedelta(seconds=6),
    )
    tampered = replace(
        attestation,
        observation_evidence_hash="sha256:" + "c" * 64,
    )

    with pytest.raises(
        ProtocolViolation,
        match="attestation digest mismatch",
    ):
        tampered.verify()


def test_attestation_detects_attempt_swap(tmp_path):
    (
        fixture,
        proposal,
        _,
        journal,
        _,
        attempt,
    ) = _prepare_context_attempt(tmp_path)
    committed = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=5),
        result={"status": "APPLIED"},
    )
    attestation = ExecutionAttestation.seal(
        attestation_id="attestation-swap",
        attempt=committed,
        outcome_contract_hash="sha256:" + "a" * 64,
        observation_evidence_hash="sha256:" + "b" * 64,
        verification_status="SUCCEEDED",
        verified_at=NOW + timedelta(seconds=6),
    )

    other_journal = SQLiteExecutionJournal(tmp_path / "other.db")
    other_provenance = ExecutionContextProvenance.seal(
        proposal=proposal,
    )
    other = other_journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW + timedelta(seconds=7),
        context_provenance=other_provenance,
    )
    other_committed = other_journal.commit(
        other,
        completed_at=NOW + timedelta(seconds=8),
        result={"status": "APPLIED"},
    )

    with pytest.raises(
        ProtocolViolation,
        match="attempt binding mismatch",
    ):
        attestation.verify_attempt(other_committed)


def test_attestation_supports_non_context_attempt(tmp_path):
    fixture = build_transition()
    journal = SQLiteExecutionJournal(tmp_path / "plain.db")
    attempt = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW,
    )
    committed = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=1),
        result={"status": "APPLIED"},
    )

    attestation = ExecutionAttestation.seal(
        attestation_id="attestation-plain",
        attempt=committed,
        outcome_contract_hash="sha256:" + "a" * 64,
        observation_evidence_hash="sha256:" + "b" * 64,
        verification_status="SUCCEEDED",
        verified_at=NOW + timedelta(seconds=2),
    )

    assert attestation.context_provenance_hash is None
    attestation.verify_attempt(committed)
