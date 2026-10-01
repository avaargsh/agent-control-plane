from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import uuid4

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
    state: ExecutionAttemptState
    prepared_at: datetime
    completed_at: datetime | None
    result_hash: str | None
    result_json: str | None

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
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
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

    @staticmethod
    def _attempt(row: sqlite3.Row) -> ExecutionAttempt:
        prepared_at = _parse_time(row["prepared_at"])
        if prepared_at is None:
            raise ProtocolViolation(
                "journal prepared_at cannot be null"
            )
        return ExecutionAttempt(
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
            state=ExecutionAttemptState(row["state"]),
            prepared_at=prepared_at,
            completed_at=_parse_time(row["completed_at"]),
            result_hash=row["result_hash"],
            result_json=row["result_json"],
        )

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

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
                    transition.subject.resource_uid,
                    action.action_hash,
                    authorization.authorization_hash,
                    ExecutionAttemptState.PREPARED.value,
                ),
            ).fetchone()
            if row is not None:
                connection.execute("COMMIT")
                return self._attempt(row)

            attempt_id = uuid4().hex
            operation_id = uuid4().hex
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
                    state,
                    prepared_at,
                    completed_at,
                    result_hash,
                    result_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
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
                    ExecutionAttemptState.PREPARED.value,
                    prepared_at.isoformat(),
                ),
            )
            connection.execute("COMMIT")

        attempt = self.get(attempt_id)
        if attempt is None:
            raise ProtocolViolation("prepared execution attempt disappeared")
        return attempt

    def _finish(
        self,
        attempt: ExecutionAttempt,
        *,
        state: ExecutionAttemptState,
        completed_at: datetime,
        result: Mapping[str, Any],
    ) -> ExecutionAttempt:
        _require_aware(completed_at, "completed_at")
        result_json = _json_snapshot(result)
        result_hash = canonical_digest(json.loads(result_json))

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
    owns_postcondition = (
        live_replicas == desired_replicas
        and generation == transition.expected_generation + 1
        and annotations.get(_ACTION_HASH_ANNOTATION) == action.action_hash
        and annotations.get(_TRANSITION_HASH_ANNOTATION)
        == transition.transition_hash
        and annotations.get(_OPERATION_ID_ANNOTATION)
        == current.operation_id
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
        }
        journal.commit(
            current,
            completed_at=reconciled_at,
            result=result,
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
        journal.abort_not_applied(
            current,
            completed_at=reconciled_at,
            result=result,
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
