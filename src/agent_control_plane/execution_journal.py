from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .authority_reservation import (
    AuthorityReservation,
    SQLiteAuthorityReservationStore,
)
from .execution_provenance import (
    ExecutionContextProvenance,
    ExecutionContextProvenanceV2,
    ExecutionContextProvenanceV3,
)
from .state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    ExecutionFence,
    ProtocolViolation,
    StateTransition,
    canonical_digest,
)


_ACTION_HASH_ANNOTATION = "agent-control-plane.openai.com/action-hash"
_TRANSITION_HASH_ANNOTATION = "agent-control-plane.openai.com/transition-hash"
_OPERATION_ID_ANNOTATION = "agent-control-plane.openai.com/operation-id"
_AUTHORITY_RESERVATION_HASH_ANNOTATION = (
    "agent-control-plane.openai.com/authority-reservation-hash"
)
_EXECUTION_CONTEXT_RESULT_KEY = "_execution_context_provenance"
_AUTHORITY_RESERVATION_RESULT_KEY = "_authority_reservation"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


def _json_snapshot(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolViolation(
            "execution journal payload must be canonical JSON"
        ) from exc


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, "stored execution timestamp")
    return parsed


class ExecutionAttemptState(str, Enum):
    PREPARED = "PREPARED"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExecutionAttempt:
    attempt_id: str
    operation_id: str
    resource_uid: str
    transition_hash: str
    action_hash: str
    authorization_hash: str
    lease_id: str
    lease_epoch: int
    expected_generation: int
    before_json: str
    desired_json: str
    context_provenance_json: str | None
    context_provenance_hash: str | None
    authority_reservation_json: str | None
    authority_reservation_hash: str | None
    attempt_hash: str
    state: ExecutionAttemptState
    prepared_at: datetime
    completed_at: datetime | None
    result_hash: str | None
    result_json: str | None

    def verify(self) -> None:
        payload = {
            "attempt_id": self.attempt_id,
            "operation_id": self.operation_id,
            "resource_uid": self.resource_uid,
            "transition_hash": self.transition_hash,
            "action_hash": self.action_hash,
            "authorization_hash": self.authorization_hash,
            "lease_id": self.lease_id,
            "lease_epoch": self.lease_epoch,
            "expected_generation": self.expected_generation,
            "before": json.loads(self.before_json),
            "desired": json.loads(self.desired_json),
            "prepared_at": self.prepared_at.isoformat(),
        }
        if (
            (self.context_provenance_json is None)
            != (self.context_provenance_hash is None)
        ):
            raise ProtocolViolation(
                "execution context provenance is only partially populated"
            )
        if self.context_provenance_json is not None:
            provenance = json.loads(self.context_provenance_json)
            if not isinstance(provenance, Mapping):
                raise ProtocolViolation(
                    "execution context provenance must be an object"
                )
            if canonical_digest(provenance) != self.context_provenance_hash:
                raise ProtocolViolation(
                    "execution context provenance digest mismatch"
                )
            payload["context_provenance"] = provenance

        if (
            (self.authority_reservation_json is None)
            != (self.authority_reservation_hash is None)
        ):
            raise ProtocolViolation(
                "execution authority reservation is only partially populated"
            )
        if self.authority_reservation_json is not None:
            reservation = json.loads(self.authority_reservation_json)
            if not isinstance(reservation, Mapping):
                raise ProtocolViolation(
                    "execution authority reservation must be an object"
                )
            AuthorityReservation.verify_binding_mapping(reservation)
            if (
                reservation["reservation_hash"]
                != self.authority_reservation_hash
            ):
                raise ProtocolViolation(
                    "execution authority reservation hash mismatch"
                )
            if reservation["operation_id"] != self.operation_id:
                raise ProtocolViolation(
                    "execution authority reservation operation mismatch"
                )
            if reservation["resource_uid"] != self.resource_uid:
                raise ProtocolViolation(
                    "execution authority reservation resource mismatch"
                )
            if reservation["lease_id"] != self.lease_id:
                raise ProtocolViolation(
                    "execution authority reservation lease id mismatch"
                )
            if int(reservation["lease_epoch"]) != self.lease_epoch:
                raise ProtocolViolation(
                    "execution authority reservation lease epoch mismatch"
                )
            provenance = self.context_provenance
            if provenance is None:
                raise ProtocolViolation(
                    "authority reservation requires context provenance"
                )
            if reservation["proposal_hash"] != provenance.get(
                "proposal_hash"
            ):
                raise ProtocolViolation(
                    "authority reservation proposal mismatch"
                )

        actual = canonical_digest(payload)
        if actual != self.attempt_hash:
            raise ProtocolViolation(
                "execution attempt digest mismatch"
            )

        if self.state is ExecutionAttemptState.PREPARED:
            if (
                self.completed_at is not None
                or self.result_hash is not None
                or self.result_json is not None
            ):
                raise ProtocolViolation(
                    "PREPARED execution attempt cannot carry terminal result"
                )
            return

        if (
            self.completed_at is None
            or self.result_hash is None
            or self.result_json is None
        ):
            raise ProtocolViolation(
                "terminal execution attempt requires result evidence"
            )
        result_value = json.loads(self.result_json)
        if canonical_digest(result_value) != self.result_hash:
            raise ProtocolViolation(
                "execution result digest mismatch"
            )
        if self.authority_reservation_json is not None:
            expected = self.authority_reservation
            if result_value.get(
                _AUTHORITY_RESERVATION_RESULT_KEY
            ) != expected:
                raise ProtocolViolation(
                    "terminal result authority reservation mismatch"
                )

    @property
    def before(self) -> Mapping[str, Any]:
        value = json.loads(self.before_json)
        if not isinstance(value, Mapping):
            raise ProtocolViolation("journal before state must be an object")
        return value

    @property
    def desired(self) -> Mapping[str, Any]:
        value = json.loads(self.desired_json)
        if not isinstance(value, Mapping):
            raise ProtocolViolation("journal desired state must be an object")
        return value

    @property
    def context_provenance(self) -> Mapping[str, Any] | None:
        if self.context_provenance_json is None:
            return None
        value = json.loads(self.context_provenance_json)
        if not isinstance(value, Mapping):
            raise ProtocolViolation(
                "journal context provenance must be an object"
            )
        return value

    @property
    def authority_reservation(self) -> Mapping[str, Any] | None:
        if self.authority_reservation_json is None:
            return None
        value = json.loads(self.authority_reservation_json)
        if not isinstance(value, Mapping):
            raise ProtocolViolation(
                "journal authority reservation must be an object"
            )
        return value

    @property
    def result(self) -> Mapping[str, Any] | None:
        if self.result_json is None:
            return None
        value = json.loads(self.result_json)
        if not isinstance(value, Mapping):
            raise ProtocolViolation("journal result must be an object")
        return value


class SQLiteExecutionJournal:
    """Durable write-ahead journal for provider execution attempts.

    PREPARED is persisted before the provider side effect. The operation_id
    from that row is then written into the provider target in the same mutation
    as the state change. A process that crashes after the side effect but before
    COMMITTED can later recover the exact attempt identity from this journal.
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
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_attempts (
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
                    context_provenance_json TEXT,
                    context_provenance_hash TEXT,
                    authority_reservation_json TEXT,
                    authority_reservation_hash TEXT,
                    attempt_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    prepared_at TEXT NOT NULL,
                    completed_at TEXT,
                    result_hash TEXT,
                    result_json TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_execution_attempts_action
                ON execution_attempts(
                    resource_uid,
                    action_hash,
                    authorization_hash,
                    state
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(execution_attempts)"
                ).fetchall()
            }
            if "context_provenance_json" not in columns:
                connection.execute(
                    "ALTER TABLE execution_attempts "
                    "ADD COLUMN context_provenance_json TEXT"
                )
            if "context_provenance_hash" not in columns:
                connection.execute(
                    "ALTER TABLE execution_attempts "
                    "ADD COLUMN context_provenance_hash TEXT"
                )
            if "authority_reservation_json" not in columns:
                connection.execute(
                    "ALTER TABLE execution_attempts "
                    "ADD COLUMN authority_reservation_json TEXT"
                )
            if "authority_reservation_hash" not in columns:
                connection.execute(
                    "ALTER TABLE execution_attempts "
                    "ADD COLUMN authority_reservation_hash TEXT"
                )

    @staticmethod
    def _attempt(row: sqlite3.Row) -> ExecutionAttempt:
        prepared_at = _parse_time(row["prepared_at"])
        if prepared_at is None:
            raise ProtocolViolation(
                "journal prepared_at cannot be null"
            )
        attempt = ExecutionAttempt(
            attempt_id=row["attempt_id"],
            operation_id=row["operation_id"],
            resource_uid=row["resource_uid"],
            transition_hash=row["transition_hash"],
            action_hash=row["action_hash"],
            authorization_hash=row["authorization_hash"],
            lease_id=row["lease_id"],
            lease_epoch=int(row["lease_epoch"]),
            expected_generation=int(row["expected_generation"]),
            before_json=row["before_json"],
            desired_json=row["desired_json"],
            context_provenance_json=row["context_provenance_json"],
            context_provenance_hash=row["context_provenance_hash"],
            authority_reservation_json=row[
                "authority_reservation_json"
            ],
            authority_reservation_hash=row[
                "authority_reservation_hash"
            ],
            attempt_hash=row["attempt_hash"],
            state=ExecutionAttemptState(row["state"]),
            prepared_at=prepared_at,
            completed_at=_parse_time(row["completed_at"]),
            result_hash=row["result_hash"],
            result_json=row["result_json"],
        )
        attempt.verify()
        return attempt

    def get(self, attempt_id: str) -> ExecutionAttempt | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM execution_attempts
                WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
        return self._attempt(row) if row is not None else None

    def has_prepared_reservation_reference(
        self,
        *,
        reservation_hash: str,
        exclude_attempt_id: str | None = None,
    ) -> bool:
        """Check raw PREPARED references without trusting row verification."""

        clauses = [
            "authority_reservation_hash = ?",
            "state = ?",
        ]
        values: list[Any] = [
            reservation_hash,
            ExecutionAttemptState.PREPARED.value,
        ]
        if exclude_attempt_id is not None:
            clauses.append("attempt_id != ?")
            values.append(exclude_attempt_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM execution_attempts WHERE "
                + " AND ".join(clauses)
                + " LIMIT 1",
                tuple(values),
            ).fetchone()
        return row is not None

    def open_for_action(
        self,
        *,
        resource_uid: str,
        action_hash: str,
        authorization_hash: str,
    ) -> ExecutionAttempt | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM execution_attempts
                WHERE resource_uid = ?
                  AND action_hash = ?
                  AND authorization_hash = ?
                  AND state = ?
                ORDER BY prepared_at DESC
                LIMIT 1
                """,
                (
                    resource_uid,
                    action_hash,
                    authorization_hash,
                    ExecutionAttemptState.PREPARED.value,
                ),
            ).fetchone()
        return self._attempt(row) if row is not None else None

    def prepare(
        self,
        *,
        transition: StateTransition,
        action: ActionIntent,
        authorization: AuthorizationBinding,
        fence: ExecutionFence,
        prepared_at: datetime,
        context_provenance: (
            ExecutionContextProvenance
            | ExecutionContextProvenanceV2
            | ExecutionContextProvenanceV3
            | None
        ) = None,
    ) -> ExecutionAttempt:
        _require_aware(prepared_at, "prepared_at")
        transition.verify()
        action.verify()
        authorization.verify()

        if action.transition_hash != transition.transition_hash:
            raise ProtocolViolation(
                "journal action is not bound to transition"
            )
        if authorization.transition_hash != transition.transition_hash:
            raise ProtocolViolation(
                "journal authorization transition mismatch"
            )
        if authorization.action_hash != action.action_hash:
            raise ProtocolViolation(
                "journal authorization action mismatch"
            )
        if fence.transition_hash != transition.transition_hash:
            raise ProtocolViolation(
                "journal fence transition mismatch"
            )
        if fence.action_hash != action.action_hash:
            raise ProtocolViolation(
                "journal fence action mismatch"
            )
        if fence.authorization_hash != authorization.authorization_hash:
            raise ProtocolViolation(
                "journal fence authorization mismatch"
            )

        before_json = _json_snapshot(transition.before)
        desired_json = _json_snapshot(transition.desired)
        context_provenance_json = None
        context_provenance_hash = None
        if context_provenance is not None:
            context_provenance.verify()
            context_provenance_json = _json_snapshot(
                context_provenance.as_mapping()
            )
            context_provenance_hash = canonical_digest(
                json.loads(context_provenance_json)
            )
            if (
                context_provenance.provenance_hash
                != context_provenance_hash
            ):
                raise ProtocolViolation(
                    "execution context provenance hash mismatch"
                )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM execution_attempts
                WHERE resource_uid = ?
                  AND action_hash = ?
                  AND authorization_hash = ?
                ORDER BY prepared_at DESC
                LIMIT 1
                """,
                (
                    transition.subject.resource_uid,
                    action.action_hash,
                    authorization.authorization_hash,
                ),
            ).fetchone()
            if row is not None:
                existing = self._attempt(row)
                if existing.state is ExecutionAttemptState.PREPARED:
                    if (
                        existing.context_provenance_hash
                        != context_provenance_hash
                    ):
                        connection.execute("ROLLBACK")
                        raise ProtocolViolation(
                            "open execution attempt context provenance "
                            "does not match"
                        )
                    if (
                        existing.lease_id != fence.lease_id
                        or existing.lease_epoch != fence.lease_epoch
                    ):
                        connection.execute("ROLLBACK")
                        raise ProtocolViolation(
                            "open execution attempt must be reconciled "
                            "before lease rebinding"
                        )
                    connection.execute("COMMIT")
                    return existing
                if existing.state is ExecutionAttemptState.COMMITTED:
                    connection.execute("ROLLBACK")
                    raise ProtocolViolation(
                        "execution action is already committed"
                    )
                if existing.state is ExecutionAttemptState.UNKNOWN:
                    connection.execute("ROLLBACK")
                    raise ProtocolViolation(
                        "execution action has UNKNOWN prior attempt"
                    )

            attempt_id = uuid4().hex
            operation_id = uuid4().hex
            attempt_payload = {
                "attempt_id": attempt_id,
                "operation_id": operation_id,
                "resource_uid": transition.subject.resource_uid,
                "transition_hash": transition.transition_hash,
                "action_hash": action.action_hash,
                "authorization_hash": authorization.authorization_hash,
                "lease_id": fence.lease_id,
                "lease_epoch": fence.lease_epoch,
                "expected_generation": transition.expected_generation,
                "before": json.loads(before_json),
                "desired": json.loads(desired_json),
                "prepared_at": prepared_at.isoformat(),
            }
            if context_provenance_json is not None:
                attempt_payload["context_provenance"] = json.loads(
                    context_provenance_json
                )
            attempt_hash = canonical_digest(attempt_payload)
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    attempt_id,
                    operation_id,
                    transition.subject.resource_uid,
                    transition.transition_hash,
                    action.action_hash,
                    authorization.authorization_hash,
                    fence.lease_id,
                    fence.lease_epoch,
                    transition.expected_generation,
                    before_json,
                    desired_json,
                    context_provenance_json,
                    context_provenance_hash,
                    attempt_hash,
                    ExecutionAttemptState.PREPARED.value,
                    prepared_at.isoformat(),
                ),
            )
            connection.execute("COMMIT")

        attempt = self.get(attempt_id)
        if attempt is None:
            raise ProtocolViolation("prepared execution attempt disappeared")
        return attempt

    def bind_authority_reservation(
        self,
        attempt: ExecutionAttempt,
        reservation: AuthorityReservation,
    ) -> ExecutionAttempt:
        """Durably attach the reservation before the provider side effect."""

        reservation.verify()
        binding = reservation.as_binding_mapping()
        binding_json = _json_snapshot(binding)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM execution_attempts
                WHERE attempt_id = ?
                """,
                (attempt.attempt_id,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "execution attempt does not exist"
                )
            current = self._attempt(row)
            if current.state is not ExecutionAttemptState.PREPARED:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation can only bind PREPARED attempt"
                )
            if reservation.operation_id != current.operation_id:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation operation does not match attempt"
                )
            if reservation.resource_uid != current.resource_uid:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation resource does not match attempt"
                )
            if reservation.lease_id != current.lease_id:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation lease id does not match attempt"
                )
            if reservation.lease_epoch != current.lease_epoch:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation lease epoch does not match attempt"
                )
            provenance = current.context_provenance
            if provenance is None:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation requires context provenance"
                )
            if reservation.proposal_hash != provenance.get(
                "proposal_hash"
            ):
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "authority reservation proposal does not match attempt"
                )
            if current.authority_reservation_json is not None:
                if (
                    current.authority_reservation_hash
                    == reservation.reservation_hash
                    and current.authority_reservation_json == binding_json
                ):
                    connection.execute("COMMIT")
                    return current
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "execution attempt already has another authority "
                    "reservation"
                )

            connection.execute(
                """
                UPDATE execution_attempts
                SET authority_reservation_json = ?,
                    authority_reservation_hash = ?
                WHERE attempt_id = ? AND state = ?
                """,
                (
                    binding_json,
                    reservation.reservation_hash,
                    current.attempt_id,
                    ExecutionAttemptState.PREPARED.value,
                ),
            )
            connection.execute("COMMIT")

        bound = self.get(attempt.attempt_id)
        if bound is None:
            raise ProtocolViolation(
                "reservation-bound execution attempt disappeared"
            )
        return bound

    def _finish(
        self,
        attempt: ExecutionAttempt,
        *,
        state: ExecutionAttemptState,
        completed_at: datetime,
        result: Mapping[str, Any],
    ) -> ExecutionAttempt:
        _require_aware(completed_at, "completed_at")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM execution_attempts
                WHERE attempt_id = ?
                """,
                (attempt.attempt_id,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "execution attempt does not exist"
                )
            current = self._attempt(row)
            effective_result = dict(result)
            provenance = current.context_provenance
            if (
                state is ExecutionAttemptState.COMMITTED
                and provenance is not None
                and provenance.get("provenance_version")
                == "execution-context-provenance/v3"
                and current.authority_reservation_json is None
            ):
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "committed provenance v3 execution requires durable "
                    "authority reservation"
                )
            if provenance is not None:
                reserved = effective_result.get(
                    _EXECUTION_CONTEXT_RESULT_KEY
                )
                expected_reserved = {
                    "provenance_hash": current.context_provenance_hash,
                    **dict(provenance),
                }
                if (
                    reserved is not None
                    and reserved != expected_reserved
                ):
                    connection.execute("ROLLBACK")
                    raise ProtocolViolation(
                        "terminal result context provenance mismatch"
                    )
                effective_result[
                    _EXECUTION_CONTEXT_RESULT_KEY
                ] = expected_reserved
            authority_reservation = current.authority_reservation
            if authority_reservation is not None:
                reserved = effective_result.get(
                    _AUTHORITY_RESERVATION_RESULT_KEY
                )
                expected_reserved = dict(authority_reservation)
                if (
                    reserved is not None
                    and reserved != expected_reserved
                ):
                    connection.execute("ROLLBACK")
                    raise ProtocolViolation(
                        "terminal result authority reservation mismatch"
                    )
                effective_result[
                    _AUTHORITY_RESERVATION_RESULT_KEY
                ] = expected_reserved
            result_json = _json_snapshot(effective_result)
            result_hash = canonical_digest(json.loads(result_json))

            if current.operation_id != attempt.operation_id:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "execution operation identity changed"
                )
            if current.state is not ExecutionAttemptState.PREPARED:
                if (
                    current.state is state
                    and current.result_hash == result_hash
                ):
                    connection.execute("COMMIT")
                    return current
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "execution attempt is already terminal"
                )

            connection.execute(
                """
                UPDATE execution_attempts
                SET state = ?,
                    completed_at = ?,
                    result_hash = ?,
                    result_json = ?
                WHERE attempt_id = ?
                """,
                (
                    state.value,
                    completed_at.isoformat(),
                    result_hash,
                    result_json,
                    attempt.attempt_id,
                ),
            )
            connection.execute("COMMIT")

        finished = self.get(attempt.attempt_id)
        if finished is None:
            raise ProtocolViolation("finished execution attempt disappeared")
        return finished

    def commit(
        self,
        attempt: ExecutionAttempt,
        *,
        completed_at: datetime,
        result: Mapping[str, Any],
    ) -> ExecutionAttempt:
        return self._finish(
            attempt,
            state=ExecutionAttemptState.COMMITTED,
            completed_at=completed_at,
            result=result,
        )

    def abort_not_applied(
        self,
        attempt: ExecutionAttempt,
        *,
        completed_at: datetime,
        result: Mapping[str, Any],
    ) -> ExecutionAttempt:
        return self._finish(
            attempt,
            state=ExecutionAttemptState.ABORTED,
            completed_at=completed_at,
            result=result,
        )

    def mark_unknown(
        self,
        attempt: ExecutionAttempt,
        *,
        completed_at: datetime,
        result: Mapping[str, Any],
    ) -> ExecutionAttempt:
        return self._finish(
            attempt,
            state=ExecutionAttemptState.UNKNOWN,
            completed_at=completed_at,
            result=result,
        )


class DeploymentReadApi(Protocol):
    def get_deployment(
        self,
        *,
        namespace: str,
        name: str,
    ) -> Mapping[str, Any] | None:
        ...


class ReconcileStatus(str, Enum):
    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class ExecutionReconcileResult:
    status: ReconcileStatus
    attempt_id: str
    operation_id: str
    observed_generation: int | None
    observed_resource_version: str | None
    reason: str
    result: Mapping[str, Any]


def reconcile_deployment_attempt(
    *,
    api: DeploymentReadApi,
    journal: SQLiteExecutionJournal,
    attempt: ExecutionAttempt,
    transition: StateTransition,
    action: ActionIntent,
    namespace: str,
    name: str,
    reconciled_at: datetime,
    authority_reservation_store: (
        SQLiteAuthorityReservationStore | None
    ) = None,
) -> ExecutionReconcileResult:
    """Resolve a PREPARED attempt after a controller/process restart."""

    _require_aware(reconciled_at, "reconciled_at")
    transition.verify()
    action.verify()

    current = journal.get(attempt.attempt_id)
    if current is None:
        raise ProtocolViolation("execution attempt does not exist")
    if current.state is not ExecutionAttemptState.PREPARED:
        raise ProtocolViolation(
            "only PREPARED execution attempts can be reconciled"
        )
    if current.transition_hash != transition.transition_hash:
        raise ProtocolViolation(
            "journal transition binding mismatch during reconcile"
        )
    if current.action_hash != action.action_hash:
        raise ProtocolViolation(
            "journal action binding mismatch during reconcile"
        )

    live = api.get_deployment(namespace=namespace, name=name)
    if live is None:
        result = {
            "status": ReconcileStatus.AMBIGUOUS.value,
            "reason": "deployment missing during reconcile",
        }
        journal.mark_unknown(
            current,
            completed_at=reconciled_at,
            result=result,
        )
        return ExecutionReconcileResult(
            status=ReconcileStatus.AMBIGUOUS,
            attempt_id=current.attempt_id,
            operation_id=current.operation_id,
            observed_generation=None,
            observed_resource_version=None,
            reason=result["reason"],
            result=result,
        )

    metadata = live.get("metadata", {})
    spec = live.get("spec", {})
    if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
        raise ProtocolViolation(
            "deployment reconcile requires metadata/spec objects"
        )
    uid = metadata.get("uid")
    generation = metadata.get("generation")
    resource_version = metadata.get("resourceVersion")
    annotations = metadata.get("annotations", {})
    if not isinstance(annotations, Mapping):
        annotations = {}

    if uid != current.resource_uid:
        result = {
            "status": ReconcileStatus.AMBIGUOUS.value,
            "reason": "deployment uid changed during reconcile",
            "observed_uid": uid,
        }
        journal.mark_unknown(
            current,
            completed_at=reconciled_at,
            result=result,
        )
        return ExecutionReconcileResult(
            status=ReconcileStatus.AMBIGUOUS,
            attempt_id=current.attempt_id,
            operation_id=current.operation_id,
            observed_generation=(
                int(generation)
                if isinstance(generation, int) and not isinstance(generation, bool)
                else None
            ),
            observed_resource_version=(
                str(resource_version)
                if resource_version is not None
                else None
            ),
            reason=result["reason"],
            result=result,
        )

    if isinstance(generation, bool) or not isinstance(generation, int):
        raise ProtocolViolation(
            "deployment generation is invalid during reconcile"
        )
    if not isinstance(resource_version, str) or not resource_version:
        raise ProtocolViolation(
            "deployment resourceVersion is invalid during reconcile"
        )

    desired_replicas = action.parameters.get("replicas")
    before_replicas = transition.before.get("replicas")
    live_replicas = spec.get("replicas")
    observed_reservation_hash = annotations.get(
        _AUTHORITY_RESERVATION_HASH_ANNOTATION
    )
    provenance = current.context_provenance
    requires_reservation = (
        provenance is not None
        and provenance.get("provenance_version")
        == "execution-context-provenance/v3"
    )
    reservation_matches = (
        (
            not requires_reservation
            and current.authority_reservation_hash is None
        )
        or (
            current.authority_reservation_hash is not None
            and observed_reservation_hash
            == current.authority_reservation_hash
        )
    )
    owns_postcondition = (
        live_replicas == desired_replicas
        and generation == transition.expected_generation + 1
        and annotations.get(_ACTION_HASH_ANNOTATION) == action.action_hash
        and annotations.get(_TRANSITION_HASH_ANNOTATION)
        == transition.transition_hash
        and annotations.get(_OPERATION_ID_ANNOTATION)
        == current.operation_id
        and reservation_matches
    )

    if owns_postcondition:
        result = {
            "status": ReconcileStatus.APPLIED.value,
            "reason": "exact operation ownership proven from provider state",
            "operation_id": current.operation_id,
            "action_hash": action.action_hash,
            "transition_hash": transition.transition_hash,
            "desired_replicas": desired_replicas,
            "after_generation": generation,
            "after_resource_version": resource_version,
            "reconstructed_after_crash": True,
            "authority_reservation_hash": observed_reservation_hash,
        }
        committed = journal.commit(
            current,
            completed_at=reconciled_at,
            result=result,
        )
        if (
            authority_reservation_store is not None
            and committed.authority_reservation is not None
        ):
            authority_reservation_store.release_after_terminal(
                committed.authority_reservation,
                now=reconciled_at,
            )
        return ExecutionReconcileResult(
            status=ReconcileStatus.APPLIED,
            attempt_id=current.attempt_id,
            operation_id=current.operation_id,
            observed_generation=generation,
            observed_resource_version=resource_version,
            reason=result["reason"],
            result=result,
        )

    safely_not_applied = (
        generation == transition.expected_generation
        and live_replicas == before_replicas
        and annotations.get(_OPERATION_ID_ANNOTATION)
        != current.operation_id
    )
    if safely_not_applied:
        result = {
            "status": ReconcileStatus.NOT_APPLIED.value,
            "reason": "provider state still matches transition precondition",
            "operation_id": current.operation_id,
            "observed_generation": generation,
            "observed_resource_version": resource_version,
        }
        aborted = journal.abort_not_applied(
            current,
            completed_at=reconciled_at,
            result=result,
        )
        if (
            authority_reservation_store is not None
            and aborted.authority_reservation is not None
        ):
            authority_reservation_store.release_after_terminal(
                aborted.authority_reservation,
                now=reconciled_at,
            )
        return ExecutionReconcileResult(
            status=ReconcileStatus.NOT_APPLIED,
            attempt_id=current.attempt_id,
            operation_id=current.operation_id,
            observed_generation=generation,
            observed_resource_version=resource_version,
            reason=result["reason"],
            result=result,
        )

    result = {
        "status": ReconcileStatus.AMBIGUOUS.value,
        "reason": "provider state cannot prove applied or not-applied",
        "operation_id": current.operation_id,
        "observed_generation": generation,
        "observed_resource_version": resource_version,
        "observed_replicas": live_replicas,
        "observed_operation_id": annotations.get(
            _OPERATION_ID_ANNOTATION
        ),
        "observed_action_hash": annotations.get(
            _ACTION_HASH_ANNOTATION
        ),
        "observed_transition_hash": annotations.get(
            _TRANSITION_HASH_ANNOTATION
        ),
        "observed_authority_reservation_hash": (
            observed_reservation_hash
        ),
        "expected_authority_reservation_hash": (
            current.authority_reservation_hash
        ),
    }
    journal.mark_unknown(
        current,
        completed_at=reconciled_at,
        result=result,
    )
    return ExecutionReconcileResult(
        status=ReconcileStatus.AMBIGUOUS,
        attempt_id=current.attempt_id,
        operation_id=current.operation_id,
        observed_generation=generation,
        observed_resource_version=resource_version,
        reason=result["reason"],
        result=result,
    )
