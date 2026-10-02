from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .authority_reservation import (
    AuthorityReservation,
    AuthorityReservationState,
    SQLiteAuthorityReservationStore,
)
from .execution_journal import (
    ExecutionAttempt,
    ExecutionAttemptState,
    SQLiteExecutionJournal,
)
from .state_transition_protocol import (
    Principal,
    ProtocolViolation,
    canonical_digest,
)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, "stored repair timestamp")
    return parsed


class ActiveLeaseLookup(Protocol):
    def active(self, resource_uid: str):
        ...


@dataclass(frozen=True)
class TerminalReservationRepairEvidence:
    repair_id: str
    reservation_id: str
    reservation_hash: str
    terminal_attempt_id: str
    terminal_attempt_hash: str
    terminal_state: str
    terminal_result_hash: str
    actor: Principal
    repaired_at: datetime
    repair_hash: str
    repair_version: str = "terminal-reservation-repair/v1"

    @classmethod
    def seal(
        cls,
        *,
        repair_id: str,
        reservation: AuthorityReservation,
        terminal_attempt: ExecutionAttempt,
        actor: Principal,
        repaired_at: datetime,
    ) -> "TerminalReservationRepairEvidence":
        _require_aware(repaired_at, "repaired_at")
        if terminal_attempt.state not in {
            ExecutionAttemptState.COMMITTED,
            ExecutionAttemptState.ABORTED,
        }:
            raise ProtocolViolation(
                "repair requires COMMITTED or ABORTED terminal proof"
            )
        if terminal_attempt.result_hash is None:
            raise ProtocolViolation(
                "repair terminal proof requires result_hash"
            )
        if (
            terminal_attempt.authority_reservation_hash
            != reservation.reservation_hash
        ):
            raise ProtocolViolation(
                "repair terminal attempt reservation hash mismatch"
            )
        binding = terminal_attempt.authority_reservation
        if (
            binding is None
            or binding.get("reservation_id")
            != reservation.reservation_id
        ):
            raise ProtocolViolation(
                "repair terminal attempt reservation id mismatch"
            )

        provisional = cls(
            repair_id=repair_id,
            reservation_id=reservation.reservation_id,
            reservation_hash=reservation.reservation_hash,
            terminal_attempt_id=terminal_attempt.attempt_id,
            terminal_attempt_hash=terminal_attempt.attempt_hash,
            terminal_state=terminal_attempt.state.value,
            terminal_result_hash=terminal_attempt.result_hash,
            actor=actor,
            repaired_at=repaired_at,
            repair_hash="",
        )
        return cls(
            repair_id=provisional.repair_id,
            reservation_id=provisional.reservation_id,
            reservation_hash=provisional.reservation_hash,
            terminal_attempt_id=provisional.terminal_attempt_id,
            terminal_attempt_hash=provisional.terminal_attempt_hash,
            terminal_state=provisional.terminal_state,
            terminal_result_hash=provisional.terminal_result_hash,
            actor=provisional.actor,
            repaired_at=provisional.repaired_at,
            repair_hash=canonical_digest(
                provisional,
                exclude=("repair_hash",),
            ),
        )

    def verify(self) -> None:
        if not all(
            (
                self.repair_id,
                self.reservation_id,
                self.reservation_hash,
                self.terminal_attempt_id,
                self.terminal_attempt_hash,
                self.terminal_result_hash,
            )
        ):
            raise ProtocolViolation("repair evidence bindings are required")
        if self.repair_version != "terminal-reservation-repair/v1":
            raise ProtocolViolation("repair evidence version is invalid")
        if self.terminal_state not in {
            ExecutionAttemptState.COMMITTED.value,
            ExecutionAttemptState.ABORTED.value,
        }:
            raise ProtocolViolation(
                "repair evidence terminal state is invalid"
            )
        _require_aware(self.repaired_at, "repaired_at")
        actual = canonical_digest(
            self,
            exclude=("repair_hash",),
        )
        if actual != self.repair_hash:
            raise ProtocolViolation("repair evidence digest mismatch")


class SQLiteTerminalReservationRepairStore:
    """Atomically release a stuck reservation with durable repair evidence."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            timeout=30.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS authority_reservation_repairs (
                    repair_id TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL UNIQUE,
                    reservation_hash TEXT NOT NULL,
                    terminal_attempt_id TEXT NOT NULL,
                    terminal_attempt_hash TEXT NOT NULL,
                    terminal_state TEXT NOT NULL,
                    terminal_result_hash TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_subject TEXT NOT NULL,
                    repaired_at TEXT NOT NULL,
                    repair_hash TEXT NOT NULL,
                    repair_version TEXT NOT NULL,
                    FOREIGN KEY(reservation_id)
                        REFERENCES work_authority_reservations(reservation_id)
                )
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> TerminalReservationRepairEvidence:
        evidence = TerminalReservationRepairEvidence(
            repair_id=row["repair_id"],
            reservation_id=row["reservation_id"],
            reservation_hash=row["reservation_hash"],
            terminal_attempt_id=row["terminal_attempt_id"],
            terminal_attempt_hash=row["terminal_attempt_hash"],
            terminal_state=row["terminal_state"],
            terminal_result_hash=row["terminal_result_hash"],
            actor=Principal(
                type=row["actor_type"],
                subject=row["actor_subject"],
            ),
            repaired_at=_parse_time(row["repaired_at"]),
            repair_hash=row["repair_hash"],
            repair_version=row["repair_version"],
        )
        evidence.verify()
        return evidence

    def get(
        self,
        reservation_id: str,
    ) -> TerminalReservationRepairEvidence | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM authority_reservation_repairs
                WHERE reservation_id = ?
                """,
                (reservation_id,),
            ).fetchone()
        return self._record(row) if row is not None else None

    def release_with_evidence(
        self,
        *,
        reservation: AuthorityReservation,
        evidence: TerminalReservationRepairEvidence,
    ) -> TerminalReservationRepairEvidence:
        reservation.verify()
        evidence.verify()
        if evidence.reservation_id != reservation.reservation_id:
            raise ProtocolViolation(
                "repair evidence reservation id mismatch"
            )
        if evidence.reservation_hash != reservation.reservation_hash:
            raise ProtocolViolation(
                "repair evidence reservation hash mismatch"
            )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing_row = connection.execute(
                    """
                    SELECT *
                    FROM authority_reservation_repairs
                    WHERE reservation_id = ?
                    """,
                    (reservation.reservation_id,),
                ).fetchone()
                if existing_row is not None:
                    existing = self._record(existing_row)
                    reservation_row = connection.execute(
                        """
                        SELECT state, reservation_hash
                        FROM work_authority_reservations
                        WHERE reservation_id = ?
                        """,
                        (reservation.reservation_id,),
                    ).fetchone()
                    if reservation_row is None:
                        raise ProtocolViolation(
                            "repaired reservation disappeared"
                        )
                    if (
                        reservation_row["state"]
                        != AuthorityReservationState.RELEASED.value
                        or reservation_row["reservation_hash"]
                        != existing.reservation_hash
                    ):
                        raise ProtocolViolation(
                            "repair evidence exists without matching "
                            "RELEASED reservation"
                        )
                    connection.execute("COMMIT")
                    return existing

                row = connection.execute(
                    """
                    SELECT state, reservation_hash
                    FROM work_authority_reservations
                    WHERE reservation_id = ?
                    """,
                    (reservation.reservation_id,),
                ).fetchone()
                if row is None:
                    raise ProtocolViolation(
                        "repair reservation does not exist"
                    )
                if row["reservation_hash"] != reservation.reservation_hash:
                    raise ProtocolViolation(
                        "repair reservation hash changed"
                    )
                if (
                    row["state"]
                    != AuthorityReservationState.ACTIVE.value
                ):
                    raise ProtocolViolation(
                        "repair requires ACTIVE authority reservation"
                    )

                # Keep release + evidence in one Authority DB transaction. If
                # the evidence write fails, the RELEASED state rolls back too.
                connection.execute(
                    """
                    UPDATE work_authority_reservations
                    SET state = ?
                    WHERE reservation_id = ? AND state = ?
                    """,
                    (
                        AuthorityReservationState.RELEASED.value,
                        reservation.reservation_id,
                        AuthorityReservationState.ACTIVE.value,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO authority_reservation_repairs (
                        repair_id,
                        reservation_id,
                        reservation_hash,
                        terminal_attempt_id,
                        terminal_attempt_hash,
                        terminal_state,
                        terminal_result_hash,
                        actor_type,
                        actor_subject,
                        repaired_at,
                        repair_hash,
                        repair_version
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence.repair_id,
                        evidence.reservation_id,
                        evidence.reservation_hash,
                        evidence.terminal_attempt_id,
                        evidence.terminal_attempt_hash,
                        evidence.terminal_state,
                        evidence.terminal_result_hash,
                        evidence.actor.type,
                        evidence.actor.subject,
                        evidence.repaired_at.isoformat(),
                        evidence.repair_hash,
                        evidence.repair_version,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        repaired = self.get(reservation.reservation_id)
        if repaired is None:
            raise ProtocolViolation("repair evidence disappeared")
        return repaired


class TerminalReservationRepair:
    """Fail-closed repair for terminal attempts with stuck reservations."""

    def __init__(
        self,
        *,
        journal: SQLiteExecutionJournal,
        reservation_store: SQLiteAuthorityReservationStore,
        repair_store: SQLiteTerminalReservationRepairStore,
        lease_lookup: ActiveLeaseLookup,
    ) -> None:
        self.journal = journal
        self.reservation_store = reservation_store
        self.repair_store = repair_store
        self.lease_lookup = lease_lookup

    def repair(
        self,
        *,
        terminal_attempt_id: str,
        actor: Principal,
        repaired_at: datetime,
    ) -> TerminalReservationRepairEvidence:
        _require_aware(repaired_at, "repaired_at")
        attempt = self.journal.get(terminal_attempt_id)
        if attempt is None:
            raise ProtocolViolation("repair terminal attempt does not exist")
        if attempt.state not in {
            ExecutionAttemptState.COMMITTED,
            ExecutionAttemptState.ABORTED,
        }:
            raise ProtocolViolation(
                "repair requires COMMITTED or ABORTED terminal proof"
            )
        binding = attempt.authority_reservation
        if binding is None or attempt.authority_reservation_hash is None:
            raise ProtocolViolation(
                "repair terminal attempt has no authority reservation"
            )

        reservation_id = str(binding["reservation_id"])
        existing_evidence = self.repair_store.get(reservation_id)
        if existing_evidence is not None:
            if (
                existing_evidence.reservation_hash
                != attempt.authority_reservation_hash
                or existing_evidence.terminal_attempt_id
                != attempt.attempt_id
                or existing_evidence.terminal_attempt_hash
                != attempt.attempt_hash
                or existing_evidence.terminal_state
                != attempt.state.value
                or existing_evidence.terminal_result_hash
                != attempt.result_hash
            ):
                raise ProtocolViolation(
                    "existing repair evidence conflicts with terminal proof"
                )
            repaired = self.reservation_store.get(reservation_id)
            if (
                repaired is None
                or repaired.state is not AuthorityReservationState.RELEASED
                or repaired.reservation_hash
                != existing_evidence.reservation_hash
            ):
                raise ProtocolViolation(
                    "repair evidence exists without matching "
                    "RELEASED reservation"
                )
            return existing_evidence

        reservation = self.reservation_store.get(reservation_id)
        if reservation is None:
            raise ProtocolViolation("repair reservation does not exist")
        if reservation.state is not AuthorityReservationState.ACTIVE:
            raise ProtocolViolation(
                "repair requires stuck ACTIVE authority reservation"
            )
        if reservation.reservation_hash != attempt.authority_reservation_hash:
            raise ProtocolViolation(
                "repair reservation does not match terminal proof"
            )

        active = self.lease_lookup.active(reservation.resource_uid)
        if (
            active is not None
            and active.lease.epoch > reservation.lease_epoch
        ):
            raise ProtocolViolation(
                "repair blocked by higher-epoch ACTIVE execution lease"
            )

        if self.journal.has_prepared_reservation_reference(
            reservation_hash=reservation.reservation_hash,
            exclude_attempt_id=attempt.attempt_id,
        ):
            raise ProtocolViolation(
                "repair blocked by PREPARED attempt reservation reference"
            )

        evidence = TerminalReservationRepairEvidence.seal(
            repair_id=uuid4().hex,
            reservation=reservation,
            terminal_attempt=attempt,
            actor=actor,
            repaired_at=repaired_at,
        )
        return self.repair_store.release_with_evidence(
            reservation=reservation,
            evidence=evidence,
        )
