from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from .state_transition_protocol import (
    ExecutionLease,
    Principal,
    ProtocolViolation,
    canonical_digest,
)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, "stored authority reservation timestamp")
    return parsed


class AuthorityReservationState(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class AuthorityReservation:
    reservation_id: str
    work_id: str
    authority_generation: int
    authority_hash: str
    proposal_hash: str
    operation_id: str
    resource_uid: str
    lease_id: str
    lease_epoch: int
    holder: Principal
    acquired_at: datetime
    expires_at: datetime
    state: AuthorityReservationState
    reservation_hash: str
    reservation_version: str = "authority-reservation/v1"

    @classmethod
    def seal(
        cls,
        *,
        reservation_id: str,
        work_id: str,
        authority_generation: int,
        authority_hash: str,
        proposal_hash: str,
        operation_id: str,
        execution_lease: ExecutionLease,
        acquired_at: datetime,
        state: AuthorityReservationState,
    ) -> "AuthorityReservation":
        if (
            not reservation_id
            or not work_id
            or not authority_hash
            or not proposal_hash
            or not operation_id
        ):
            raise ProtocolViolation(
                "authority reservation id/work/authority/proposal/operation "
                "bindings are required"
            )
        if authority_generation <= 0:
            raise ProtocolViolation(
                "authority reservation generation must be positive"
            )
        _require_aware(acquired_at, "acquired_at")
        execution_lease.assert_active(acquired_at)
        provisional = cls(
            reservation_id=reservation_id,
            work_id=work_id,
            authority_generation=authority_generation,
            authority_hash=authority_hash,
            proposal_hash=proposal_hash,
            operation_id=operation_id,
            resource_uid=execution_lease.resource_uid,
            lease_id=execution_lease.lease_id,
            lease_epoch=execution_lease.epoch,
            holder=execution_lease.holder,
            acquired_at=acquired_at,
            expires_at=execution_lease.expires_at,
            state=state,
            reservation_hash="",
        )
        return cls(
            reservation_id=provisional.reservation_id,
            work_id=provisional.work_id,
            authority_generation=provisional.authority_generation,
            authority_hash=provisional.authority_hash,
            proposal_hash=provisional.proposal_hash,
            operation_id=provisional.operation_id,
            resource_uid=provisional.resource_uid,
            lease_id=provisional.lease_id,
            lease_epoch=provisional.lease_epoch,
            holder=provisional.holder,
            acquired_at=provisional.acquired_at,
            expires_at=provisional.expires_at,
            state=provisional.state,
            reservation_hash=canonical_digest(
                provisional,
                exclude=("reservation_hash", "state"),
            ),
        )

    def verify(self) -> None:
        if (
            not self.reservation_id
            or not self.work_id
            or not self.authority_hash
            or not self.proposal_hash
            or not self.operation_id
        ):
            raise ProtocolViolation(
                "authority reservation bindings are required"
            )
        if (
            self.authority_generation <= 0
            or self.lease_epoch <= 0
            or not self.resource_uid
        ):
            raise ProtocolViolation(
                "authority reservation generation/lease epoch must be positive"
            )
        if not isinstance(self.holder, Principal):
            raise ProtocolViolation(
                "authority reservation holder must be a Principal"
            )
        if not isinstance(self.state, AuthorityReservationState):
            raise ProtocolViolation(
                "authority reservation state is invalid"
            )
        _require_aware(self.acquired_at, "acquired_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.acquired_at:
            raise ProtocolViolation(
                "authority reservation expiry must follow acquisition"
            )
        actual = canonical_digest(
            self,
            exclude=("reservation_hash", "state"),
        )
        if actual != self.reservation_hash:
            raise ProtocolViolation(
                "authority reservation digest mismatch"
            )

    def assert_bound_lease(
        self,
        execution_lease: ExecutionLease,
        *,
        now: datetime,
    ) -> None:
        self.verify()
        execution_lease.assert_active(now)
        if execution_lease.resource_uid != self.resource_uid:
            raise ProtocolViolation(
                "authority reservation execution resource mismatch"
            )
        if execution_lease.lease_id != self.lease_id:
            raise ProtocolViolation(
                "authority reservation execution lease id mismatch"
            )
        if execution_lease.epoch != self.lease_epoch:
            raise ProtocolViolation(
                "authority reservation execution lease epoch mismatch"
            )
        if execution_lease.holder != self.holder:
            raise ProtocolViolation(
                "authority reservation execution lease holder mismatch"
            )
        if execution_lease.expires_at != self.expires_at:
            raise ProtocolViolation(
                "authority reservation execution lease expiry mismatch"
            )


def initialize_authority_reservations(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS work_authority_reservations (
            reservation_id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL,
            authority_generation INTEGER NOT NULL
                CHECK (authority_generation > 0),
            authority_hash TEXT NOT NULL,
            proposal_hash TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            resource_uid TEXT NOT NULL,
            lease_id TEXT NOT NULL,
            lease_epoch INTEGER NOT NULL CHECK (lease_epoch > 0),
            holder_type TEXT NOT NULL,
            holder_subject TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            state TEXT NOT NULL,
            reservation_hash TEXT NOT NULL,
            FOREIGN KEY(work_id) REFERENCES work_snapshots(work_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_authority_reservation_work_state
        ON work_authority_reservations(work_id, state, acquired_at)
        """
    )


def _expire_stale(
    connection: sqlite3.Connection,
    *,
    work_id: str,
    now: datetime,
) -> None:
    _require_aware(now, "now")
    rows = connection.execute(
        """
        SELECT reservation_id, expires_at
        FROM work_authority_reservations
        WHERE work_id = ? AND state = ?
        """,
        (work_id, AuthorityReservationState.ACTIVE.value),
    ).fetchall()
    expired_ids = [
        row["reservation_id"]
        for row in rows
        if _parse_time(row["expires_at"]) <= now
    ]
    for reservation_id in expired_ids:
        connection.execute(
            """
            UPDATE work_authority_reservations
            SET state = ?
            WHERE reservation_id = ? AND state = ?
            """,
            (
                AuthorityReservationState.EXPIRED.value,
                reservation_id,
                AuthorityReservationState.ACTIVE.value,
            ),
        )


def assert_authority_mutation_allowed(
    connection: sqlite3.Connection,
    *,
    work_id: str,
    now: datetime,
) -> None:
    """Fail if an unexpired execution reservation freezes this authority."""

    initialize_authority_reservations(connection)
    _expire_stale(
        connection,
        work_id=work_id,
        now=now,
    )
    row = connection.execute(
        """
        SELECT reservation_id, lease_id, lease_epoch
        FROM work_authority_reservations
        WHERE work_id = ? AND state = ?
        ORDER BY acquired_at DESC
        LIMIT 1
        """,
        (work_id, AuthorityReservationState.ACTIVE.value),
    ).fetchone()
    if row is not None:
        raise ProtocolViolation(
            "authority mutation blocked by active execution reservation: "
            f"{row['reservation_id']} lease={row['lease_id']} "
            f"epoch={row['lease_epoch']}"
        )


class SQLiteAuthorityReservationStore:
    """Freeze one AuthorityGeneration for the lifetime of an execution lease.

    This is not another execution lease. The existing ExecutionLease fences the
    provider target and establishes execution ownership. The authority
    reservation only prevents Work authority from moving between freshness
    validation and the provider side effect.
    """

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
            initialize_authority_reservations(connection)

    @staticmethod
    def _record(row: sqlite3.Row) -> AuthorityReservation:
        record = AuthorityReservation(
            reservation_id=row["reservation_id"],
            work_id=row["work_id"],
            authority_generation=int(row["authority_generation"]),
            authority_hash=row["authority_hash"],
            proposal_hash=row["proposal_hash"],
            operation_id=row["operation_id"],
            resource_uid=row["resource_uid"],
            lease_id=row["lease_id"],
            lease_epoch=int(row["lease_epoch"]),
            holder=Principal(
                type=row["holder_type"],
                subject=row["holder_subject"],
            ),
            acquired_at=_parse_time(row["acquired_at"]),
            expires_at=_parse_time(row["expires_at"]),
            state=AuthorityReservationState(row["state"]),
            reservation_hash=row["reservation_hash"],
        )
        record.verify()
        return record

    def _get(
        self,
        connection: sqlite3.Connection,
        reservation_id: str,
    ) -> AuthorityReservation | None:
        row = connection.execute(
            """
            SELECT *
            FROM work_authority_reservations
            WHERE reservation_id = ?
            """,
            (reservation_id,),
        ).fetchone()
        return self._record(row) if row is not None else None

    def acquire(
        self,
        *,
        reservation_id: str,
        work_id: str,
        expected_authority_generation: int,
        expected_authority_hash: str,
        proposal_hash: str,
        operation_id: str,
        execution_lease: ExecutionLease,
        now: datetime,
    ) -> AuthorityReservation:
        _require_aware(now, "now")
        execution_lease.assert_active(now)
        if not reservation_id:
            raise ProtocolViolation(
                "authority reservation_id is required"
            )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            initialize_authority_reservations(connection)
            _expire_stale(
                connection,
                work_id=work_id,
                now=now,
            )

            head = connection.execute(
                """
                SELECT generation, authority_hash
                FROM work_authority_heads
                WHERE work_id = ?
                """,
                (work_id,),
            ).fetchone()
            if head is None:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation requires an AuthorityHead"
                )
            generation = int(head["generation"])
            authority_hash = str(head["authority_hash"])
            if generation != expected_authority_generation:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation generation is stale: "
                    f"expected {expected_authority_generation}, "
                    f"current {generation}"
                )
            if authority_hash != expected_authority_hash:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation hash is stale"
                )

            active_row = connection.execute(
                """
                SELECT *
                FROM work_authority_reservations
                WHERE work_id = ? AND state = ?
                ORDER BY acquired_at DESC
                LIMIT 1
                """,
                (work_id, AuthorityReservationState.ACTIVE.value),
            ).fetchone()
            if active_row is not None:
                active = self._record(active_row)
                if (
                    active.resource_uid == execution_lease.resource_uid
                    and active.lease_epoch < execution_lease.epoch
                ):
                    connection.execute(
                        """
                        UPDATE work_authority_reservations
                        SET state = ?
                        WHERE reservation_id = ? AND state = ?
                        """,
                        (
                            AuthorityReservationState.SUPERSEDED.value,
                            active.reservation_id,
                            AuthorityReservationState.ACTIVE.value,
                        ),
                    )
                else:
                    connection.execute("ROLLBACK")
                    raise ProtocolViolation(
                        "authority is already reserved by another execution"
                    )

            reservation = AuthorityReservation.seal(
                reservation_id=reservation_id,
                work_id=work_id,
                authority_generation=generation,
                authority_hash=authority_hash,
                proposal_hash=proposal_hash,
                operation_id=operation_id,
                execution_lease=execution_lease,
                acquired_at=now,
                state=AuthorityReservationState.ACTIVE,
            )
            connection.execute(
                """
                INSERT INTO work_authority_reservations (
                    reservation_id,
                    work_id,
                    authority_generation,
                    authority_hash,
                    proposal_hash,
                    operation_id,
                    resource_uid,
                    lease_id,
                    lease_epoch,
                    holder_type,
                    holder_subject,
                    acquired_at,
                    expires_at,
                    state,
                    reservation_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation.reservation_id,
                    reservation.work_id,
                    reservation.authority_generation,
                    reservation.authority_hash,
                    reservation.proposal_hash,
                    reservation.operation_id,
                    reservation.resource_uid,
                    reservation.lease_id,
                    reservation.lease_epoch,
                    reservation.holder.type,
                    reservation.holder.subject,
                    reservation.acquired_at.isoformat(),
                    reservation.expires_at.isoformat(),
                    reservation.state.value,
                    reservation.reservation_hash,
                ),
            )
            connection.execute("COMMIT")
        return reservation

    def assert_active(
        self,
        reservation: AuthorityReservation,
        *,
        execution_lease: ExecutionLease,
        now: datetime,
    ) -> AuthorityReservation:
        _require_aware(now, "now")
        reservation.assert_bound_lease(
            execution_lease,
            now=now,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _expire_stale(
                connection,
                work_id=reservation.work_id,
                now=now,
            )
            current = self._get(
                connection,
                reservation.reservation_id,
            )
            connection.execute("COMMIT")
        if current is None:
            raise ProtocolViolation(
                "authority reservation does not exist"
            )
        if current.state is not AuthorityReservationState.ACTIVE:
            raise ProtocolViolation(
                "authority reservation is not ACTIVE: "
                f"{current.state.value}"
            )
        if current.reservation_hash != reservation.reservation_hash:
            raise ProtocolViolation(
                "authority reservation record changed"
            )
        current.assert_bound_lease(
            execution_lease,
            now=now,
        )
        return current

    def release(
        self,
        reservation: AuthorityReservation,
        *,
        execution_lease: ExecutionLease,
        now: datetime,
    ) -> AuthorityReservation:
        _require_aware(now, "now")
        reservation.assert_bound_lease(
            execution_lease,
            now=now,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _expire_stale(
                connection,
                work_id=reservation.work_id,
                now=now,
            )
            current = self._get(
                connection,
                reservation.reservation_id,
            )
            if current is None:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation does not exist"
                )
            if current.state is not AuthorityReservationState.ACTIVE:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "only ACTIVE authority reservation may be released"
                )
            if current.reservation_hash != reservation.reservation_hash:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation record changed"
                )
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
            row = connection.execute(
                """
                SELECT *
                FROM work_authority_reservations
                WHERE reservation_id = ?
                """,
                (reservation.reservation_id,),
            ).fetchone()
            connection.execute("COMMIT")
        if row is None:
            raise ProtocolViolation(
                "released authority reservation disappeared"
            )
        return self._record(row)
