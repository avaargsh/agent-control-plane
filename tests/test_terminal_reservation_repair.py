import sqlite3
from datetime import timedelta

import pytest

from agent_control_plane.authority_reservation import (
    AuthorityReservationState,
    SQLiteAuthorityReservationStore,
)
from agent_control_plane.execution_fencing import SQLiteExecutionLeaseStore
from agent_control_plane.execution_journal import (
    ExecutionAttemptState,
    SQLiteExecutionJournal,
)
from agent_control_plane.state_transition_protocol import (
    Principal,
    ProtocolViolation,
)
from agent_control_plane.terminal_reservation_repair import (
    SQLiteTerminalReservationRepairStore,
    TerminalReservationRepair,
)
from context_testkit import NOW, prepare_context_attempt_v3


REPAIR_ACTOR = Principal(
    type="controller",
    subject="agent-control-plane/repair-controller",
)


def _stuck_terminal(tmp_path, *, state=ExecutionAttemptState.COMMITTED):
    (
        fixture,
        store,
        _,
        _,
        _,
        context,
        journal,
        _,
        attempt,
    ) = prepare_context_attempt_v3(tmp_path)

    if state is ExecutionAttemptState.COMMITTED:
        terminal = journal.commit(
            attempt,
            completed_at=NOW + timedelta(seconds=7),
            result={"status": "APPLIED"},
        )
    elif state is ExecutionAttemptState.ABORTED:
        terminal = journal.abort_not_applied(
            attempt,
            completed_at=NOW + timedelta(seconds=7),
            result={"status": "NOT_APPLIED"},
        )
    else:
        raise AssertionError("unsupported terminal test state")

    reservations = SQLiteAuthorityReservationStore(store.path)
    reservation = reservations.get(
        context.authority_reservation.reservation_id
    )
    assert reservation is not None
    assert reservation.state is AuthorityReservationState.ACTIVE

    lease_store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")
    repair_store = SQLiteTerminalReservationRepairStore(store.path)
    service = TerminalReservationRepair(
        journal=journal,
        reservation_store=reservations,
        repair_store=repair_store,
        lease_lookup=lease_store,
    )
    return (
        fixture,
        store,
        journal,
        terminal,
        reservations,
        reservation,
        lease_store,
        repair_store,
        service,
    )


@pytest.mark.parametrize(
    "terminal_state",
    [
        ExecutionAttemptState.COMMITTED,
        ExecutionAttemptState.ABORTED,
    ],
)
def test_terminal_repair_releases_only_from_durable_terminal_proof(
    tmp_path,
    terminal_state,
):
    (
        _,
        _,
        journal,
        terminal,
        reservations,
        reservation,
        _,
        repair_store,
        service,
    ) = _stuck_terminal(tmp_path, state=terminal_state)

    before = journal.get(terminal.attempt_id)
    evidence = service.repair(
        terminal_attempt_id=terminal.attempt_id,
        actor=REPAIR_ACTOR,
        repaired_at=NOW + timedelta(seconds=8),
    )
    after = journal.get(terminal.attempt_id)

    durable = reservations.get(reservation.reservation_id)
    assert durable is not None
    assert durable.state is AuthorityReservationState.RELEASED
    assert evidence.reservation_hash == reservation.reservation_hash
    assert evidence.terminal_attempt_id == terminal.attempt_id
    assert evidence.terminal_state == terminal_state.value
    assert evidence.terminal_result_hash == terminal.result_hash
    assert evidence.actor == REPAIR_ACTOR
    assert repair_store.get(reservation.reservation_id) == evidence

    # Repair evidence never rewrites the terminal receipt.
    assert after == before
    assert after.result_hash == terminal.result_hash
    assert after.result_json == terminal.result_json


def test_committed_after_release_before_repair_is_idempotent_across_restart(
    tmp_path,
):
    (
        _,
        store,
        journal,
        terminal,
        reservations,
        reservation,
        lease_store,
        repair_store,
        service,
    ) = _stuck_terminal(tmp_path)

    first = service.repair(
        terminal_attempt_id=terminal.attempt_id,
        actor=REPAIR_ACTOR,
        repaired_at=NOW + timedelta(seconds=8),
    )

    restarted = TerminalReservationRepair(
        journal=SQLiteExecutionJournal(journal.path),
        reservation_store=SQLiteAuthorityReservationStore(store.path),
        repair_store=SQLiteTerminalReservationRepairStore(store.path),
        lease_lookup=SQLiteExecutionLeaseStore(lease_store.path),
    )
    second = restarted.repair(
        terminal_attempt_id=terminal.attempt_id,
        actor=REPAIR_ACTOR,
        repaired_at=NOW + timedelta(seconds=20),
    )

    assert second == first
    assert second.repair_hash == first.repair_hash
    assert (
        reservations.get(reservation.reservation_id).state
        is AuthorityReservationState.RELEASED
    )

    with sqlite3.connect(store.path) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM authority_reservation_repairs
            WHERE reservation_id = ?
            """,
            (reservation.reservation_id,),
        ).fetchone()[0]
    assert count == 1


def test_repair_transaction_rolls_back_partial_release_when_evidence_write_fails(
    tmp_path,
):
    (
        _,
        store,
        journal,
        terminal,
        reservations,
        reservation,
        _,
        repair_store,
        service,
    ) = _stuck_terminal(tmp_path)

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_repair_evidence_insert
            BEFORE INSERT ON authority_reservation_repairs
            BEGIN
                SELECT RAISE(ABORT, 'simulated repair evidence crash');
            END
            """
        )
        connection.commit()

    before = journal.get(terminal.attempt_id)
    with pytest.raises(sqlite3.IntegrityError):
        service.repair(
            terminal_attempt_id=terminal.attempt_id,
            actor=REPAIR_ACTOR,
            repaired_at=NOW + timedelta(seconds=8),
        )

    # UPDATE RELEASED happened earlier in the transaction, but the failed
    # evidence INSERT rolls the whole Authority DB transaction back.
    durable = reservations.get(reservation.reservation_id)
    assert durable is not None
    assert durable.state is AuthorityReservationState.ACTIVE
    assert repair_store.get(reservation.reservation_id) is None
    assert journal.get(terminal.attempt_id) == before

    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER fail_repair_evidence_insert")
        connection.commit()

    repaired = service.repair(
        terminal_attempt_id=terminal.attempt_id,
        actor=REPAIR_ACTOR,
        repaired_at=NOW + timedelta(seconds=9),
    )
    assert repaired.reservation_hash == reservation.reservation_hash
    assert (
        reservations.get(reservation.reservation_id).state
        is AuthorityReservationState.RELEASED
    )


def test_repair_refuses_higher_epoch_active_lease(tmp_path):
    (
        fixture,
        _,
        _,
        terminal,
        reservations,
        reservation,
        lease_store,
        repair_store,
        _,
    ) = _stuck_terminal(tmp_path)

    holder = Principal(
        type="controller",
        subject="agent-control-plane/new-owner",
    )
    last = None
    for index in range(fixture["lease"].epoch + 1):
        prepared = lease_store.prepare(
            resource_uid=reservation.resource_uid,
            holder=holder,
            now=NOW + timedelta(seconds=8 + index),
            ttl_seconds=600,
        )
        last = lease_store.activate(
            resource_uid=reservation.resource_uid,
            lease_id=prepared.lease.lease_id,
            epoch=prepared.lease.epoch,
            now=NOW + timedelta(seconds=8 + index),
        )
    assert last is not None
    assert last.lease.epoch > reservation.lease_epoch

    service = TerminalReservationRepair(
        journal=SQLiteExecutionJournal(
            tmp_path / "execution-v3.db"
        ),
        reservation_store=reservations,
        repair_store=repair_store,
        lease_lookup=lease_store,
    )

    with pytest.raises(
        ProtocolViolation,
        match="higher-epoch ACTIVE execution lease",
    ):
        service.repair(
            terminal_attempt_id=terminal.attempt_id,
            actor=REPAIR_ACTOR,
            repaired_at=NOW + timedelta(seconds=30),
        )

    assert (
        reservations.get(reservation.reservation_id).state
        is AuthorityReservationState.ACTIVE
    )
    assert repair_store.get(reservation.reservation_id) is None


def test_repair_refuses_another_prepared_reference_to_same_reservation(
    tmp_path,
):
    (
        _,
        _,
        journal,
        terminal,
        reservations,
        reservation,
        _,
        repair_store,
        service,
    ) = _stuck_terminal(tmp_path)

    # A valid journal cannot normally create this state because operation_id is
    # unique and the reservation digest binds that operation. Insert a corrupt
    # raw row to prove repair checks the defensive invariant before release.
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            """
            INSERT INTO execution_attempts (
                attempt_id,
                operation_id,
                resource_uid,
                transition_hash,
                action_hash,
                authorization_hash,
                lease_id,
                lease_epoch,
                expected_generation,
                before_json,
                desired_json,
                context_provenance_json,
                context_provenance_hash,
                authority_reservation_json,
                authority_reservation_hash,
                attempt_hash,
                state,
                prepared_at,
                completed_at,
                result_hash,
                result_json
            )
            SELECT
                'conflicting-prepared-attempt',
                'conflicting-operation',
                resource_uid,
                transition_hash,
                action_hash,
                authorization_hash,
                lease_id,
                lease_epoch,
                expected_generation,
                before_json,
                desired_json,
                context_provenance_json,
                context_provenance_hash,
                authority_reservation_json,
                authority_reservation_hash,
                attempt_hash,
                'PREPARED',
                prepared_at,
                NULL,
                NULL,
                NULL
            FROM execution_attempts
            WHERE attempt_id = ?
            """,
            (terminal.attempt_id,),
        )
        connection.commit()

    with pytest.raises(
        ProtocolViolation,
        match="PREPARED attempt reservation reference",
    ):
        service.repair(
            terminal_attempt_id=terminal.attempt_id,
            actor=REPAIR_ACTOR,
            repaired_at=NOW + timedelta(seconds=8),
        )

    assert (
        reservations.get(reservation.reservation_id).state
        is AuthorityReservationState.ACTIVE
    )
    assert repair_store.get(reservation.reservation_id) is None


def test_unknown_attempt_cannot_authorize_repair(tmp_path):
    (
        fixture,
        store,
        _,
        _,
        _,
        context,
        journal,
        _,
        attempt,
    ) = prepare_context_attempt_v3(tmp_path)
    unknown = journal.mark_unknown(
        attempt,
        completed_at=NOW + timedelta(seconds=7),
        result={"status": "AMBIGUOUS"},
    )
    reservations = SQLiteAuthorityReservationStore(store.path)
    repair_store = SQLiteTerminalReservationRepairStore(store.path)
    service = TerminalReservationRepair(
        journal=journal,
        reservation_store=reservations,
        repair_store=repair_store,
        lease_lookup=SQLiteExecutionLeaseStore(
            tmp_path / "unknown-leases.db"
        ),
    )

    with pytest.raises(
        ProtocolViolation,
        match="COMMITTED or ABORTED terminal proof",
    ):
        service.repair(
            terminal_attempt_id=unknown.attempt_id,
            actor=REPAIR_ACTOR,
            repaired_at=NOW + timedelta(seconds=8),
        )

    durable = reservations.get(
        context.authority_reservation.reservation_id
    )
    assert durable is not None
    assert durable.state is AuthorityReservationState.ACTIVE
    assert fixture["api"].patch_calls == 0
