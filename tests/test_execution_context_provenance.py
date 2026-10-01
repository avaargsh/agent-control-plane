import sqlite3
from datetime import timedelta

import pytest

from agent_control_plane.execution_journal import (
    ExecutionAttemptState,
    ExecutionContextProvenance,
    ReconcileStatus,
    SQLiteExecutionJournal,
    reconcile_deployment_attempt,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation
from test_kubernetes_context_bound_execution import (
    _build_context_bound_execution,
)
from test_kubernetes_deployment_transition import NOW


def _prepare_context_attempt(tmp_path):
    fixture, _, proposal, context = _build_context_bound_execution(tmp_path)
    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    provenance = ExecutionContextProvenance.seal(proposal=proposal)
    attempt = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW + timedelta(seconds=4),
        context_provenance=provenance,
    )
    return fixture, proposal, context, journal, provenance, attempt


def test_prepared_attempt_persists_context_provenance_across_restart(
    tmp_path,
):
    (
        _,
        proposal,
        _,
        journal,
        provenance,
        attempt,
    ) = _prepare_context_attempt(tmp_path)

    restarted = SQLiteExecutionJournal(journal.path)
    recovered = restarted.get(attempt.attempt_id)

    assert recovered is not None
    assert recovered.state is ExecutionAttemptState.PREPARED
    assert (
        recovered.context_provenance_hash
        == provenance.provenance_hash
    )
    assert recovered.context_provenance["work_id"] == proposal.work_id
    assert (
        recovered.context_provenance["work_version"]
        == proposal.work_version
    )
    assert (
        recovered.context_provenance["work_snapshot_hash"]
        == proposal.work_snapshot_hash
    )
    assert (
        recovered.context_provenance["projection_hash"]
        == proposal.projection_hash
    )
    assert (
        recovered.context_provenance["proposal_hash"]
        == proposal.proposal_hash
    )


def test_terminal_receipt_hash_binds_context_provenance(tmp_path):
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
        result={
            "status": "APPLIED",
            "operation_id": attempt.operation_id,
        },
    )

    bound = committed.result["_execution_context_provenance"]
    assert bound["provenance_hash"] == provenance.provenance_hash
    assert bound["proposal_hash"] == provenance.proposal_hash
    assert committed.result_hash is not None
    committed.verify()


def test_crash_reconcile_preserves_context_provenance_in_receipt(tmp_path):
    (
        fixture,
        _,
        context,
        journal,
        provenance,
        attempt,
    ) = _prepare_context_attempt(tmp_path)

    receipt = KubernetesDeploymentScaleProvider(
        fixture["api"],
    ).execute_context_bound(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        caller=fixture["holder"],
        now=NOW + timedelta(seconds=5),
        operation_id=attempt.operation_id,
        context_binding=context,
    )
    assert receipt.changed is True
    assert journal.get(attempt.attempt_id).state is ExecutionAttemptState.PREPARED

    restarted = SQLiteExecutionJournal(journal.path)
    reconciled = reconcile_deployment_attempt(
        api=fixture["api"],
        journal=restarted,
        attempt=restarted.get(attempt.attempt_id),
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=6),
    )

    assert reconciled.status is ReconcileStatus.APPLIED
    committed = restarted.get(attempt.attempt_id)
    assert committed.state is ExecutionAttemptState.COMMITTED
    assert (
        committed.result["_execution_context_provenance"][
            "provenance_hash"
        ]
        == provenance.provenance_hash
    )


def test_open_attempt_rejects_different_context_provenance(tmp_path):
    fixture, _, _, journal, _, _ = _prepare_context_attempt(tmp_path)

    with pytest.raises(
        ProtocolViolation,
        match="context provenance does not match",
    ):
        journal.prepare(
            transition=fixture["transition"],
            action=fixture["action"],
            authorization=fixture["authorization"],
            fence=fixture["fence"],
            prepared_at=NOW + timedelta(seconds=5),
            context_provenance=None,
        )


def test_tampered_context_provenance_is_rejected_by_digest(tmp_path):
    _, _, _, journal, _, attempt = _prepare_context_attempt(tmp_path)

    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            """
            UPDATE execution_attempts
            SET context_provenance_json = ?
            WHERE attempt_id = ?
            """,
            (
                '{"projection_hash":"sha256:tampered"}',
                attempt.attempt_id,
            ),
        )
        connection.commit()

    with pytest.raises(
        ProtocolViolation,
        match="context provenance digest mismatch",
    ):
        journal.get(attempt.attempt_id)


def test_legacy_journal_schema_is_migrated_without_invalidating_old_attempt(
    tmp_path,
):
    path = tmp_path / "legacy.db"
    fixture, _, _, _, _, _ = _prepare_context_attempt(tmp_path / "seed")

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE execution_attempts (
                attempt_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL UNIQUE,
                resource_uid TEXT NOT NULL,
                transition_hash TEXT NOT NULL,
                action_hash TEXT NOT NULL,
                authorization_hash TEXT NOT NULL,
                lease_id TEXT NOT NULL,
                lease_epoch INTEGER NOT NULL CHECK (lease_epoch > 0),
                expected_generation INTEGER NOT NULL,
                before_json TEXT NOT NULL,
                desired_json TEXT NOT NULL,
                attempt_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                prepared_at TEXT NOT NULL,
                completed_at TEXT,
                result_hash TEXT,
                result_json TEXT
            )
            """
        )

    journal = SQLiteExecutionJournal(path)
    attempt = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW + timedelta(seconds=4),
    )

    assert attempt.context_provenance is None
    assert attempt.context_provenance_hash is None

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(execution_attempts)"
            )
        }
    assert "context_provenance_json" in columns
    assert "context_provenance_hash" in columns
