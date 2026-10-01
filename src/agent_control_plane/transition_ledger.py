from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .execution_journal import (
    ExecutionAttempt,
    ExecutionAttemptState,
)
from .kubernetes_deployment_transition import (
    OutcomeVerificationResult,
    VerificationStatus,
)
from .policy_replay import (
    PolicyDecisionRecord,
    PolicyEffect,
    TransitionPolicyInput,
)
from .state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    ExecutionFence,
    ExecutionLease,
    Principal,
    ProtocolViolation,
    StateTransition,
    canonical_digest,
)
from .transition_approval import TransitionApproval


class TransitionLedgerConflict(ProtocolViolation):
    """The durable transition state changed since the caller observed it."""


class TransitionPhase(str, Enum):
    PROPOSED = "PROPOSED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    DENIED = "DENIED"
    AUTHORIZED = "AUTHORIZED"
    LEASED = "LEASED"
    EXECUTION_PREPARED = "EXECUTION_PREPARED"
    RECONCILING = "RECONCILING"
    EXECUTION_NOT_APPLIED = "EXECUTION_NOT_APPLIED"
    EXECUTED = "EXECUTED"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    RECOVERING = "RECOVERING"
    FROZEN = "FROZEN"
    RELEASED = "RELEASED"


_ALLOWED: Mapping[TransitionPhase, frozenset[TransitionPhase]] = {
    TransitionPhase.PROPOSED: frozenset({
        TransitionPhase.POLICY_EVALUATED,
    }),
    TransitionPhase.POLICY_EVALUATED: frozenset({
        TransitionPhase.AWAITING_APPROVAL,
        TransitionPhase.DENIED,
    }),
    TransitionPhase.AWAITING_APPROVAL: frozenset({
        TransitionPhase.AUTHORIZED,
        TransitionPhase.DENIED,
    }),
    TransitionPhase.AUTHORIZED: frozenset({
        TransitionPhase.LEASED,
        TransitionPhase.FROZEN,
    }),
    TransitionPhase.LEASED: frozenset({
        TransitionPhase.EXECUTION_PREPARED,
        TransitionPhase.FROZEN,
    }),
    TransitionPhase.EXECUTION_PREPARED: frozenset({
        TransitionPhase.RECONCILING,
        TransitionPhase.EXECUTED,
        TransitionPhase.EXECUTION_NOT_APPLIED,
        TransitionPhase.FROZEN,
    }),
    TransitionPhase.RECONCILING: frozenset({
        TransitionPhase.EXECUTED,
        TransitionPhase.EXECUTION_NOT_APPLIED,
        TransitionPhase.FROZEN,
    }),
    TransitionPhase.EXECUTION_NOT_APPLIED: frozenset({
        TransitionPhase.EXECUTION_PREPARED,
        TransitionPhase.FROZEN,
    }),
    TransitionPhase.EXECUTED: frozenset({
        TransitionPhase.VERIFYING,
        TransitionPhase.FROZEN,
    }),
    TransitionPhase.VERIFYING: frozenset({
        TransitionPhase.SUCCEEDED,
        TransitionPhase.RECOVERY_REQUIRED,
        TransitionPhase.FROZEN,
    }),
    TransitionPhase.SUCCEEDED: frozenset({
        TransitionPhase.RELEASED,
    }),
    TransitionPhase.RECOVERY_REQUIRED: frozenset({
        TransitionPhase.RECOVERING,
        TransitionPhase.FROZEN,
    }),
    TransitionPhase.RECOVERING: frozenset({
        TransitionPhase.RELEASED,
        TransitionPhase.FROZEN,
    }),
    TransitionPhase.DENIED: frozenset(),
    TransitionPhase.FROZEN: frozenset(),
    TransitionPhase.RELEASED: frozenset(),
}


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


def _canonical_json(value: Mapping[str, Any]) -> str:
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
            "transition ledger value must be canonical JSON"
        ) from exc


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, "stored transition timestamp")
    return parsed


def _actor_json(actor: Principal) -> str:
    return _canonical_json({
        "type": actor.type,
        "subject": actor.subject,
    })


@dataclass(frozen=True)
class TransitionLedgerRecord:
    transition_id: str
    transition_hash: str
    phase: TransitionPhase
    state_version: int
    facts_json: str
    parent_transition_hash: str | None
    created_at: datetime
    updated_at: datetime
    last_event_hash: str

    @property
    def facts(self) -> Mapping[str, Any]:
        value = json.loads(self.facts_json)
        if not isinstance(value, Mapping):
            raise ProtocolViolation("ledger facts must decode to an object")
        return value


@dataclass(frozen=True)
class TransitionLedgerEvent:
    event_id: str
    transition_id: str
    transition_hash: str
    from_phase: TransitionPhase | None
    to_phase: TransitionPhase
    state_version: int
    event_type: str
    actor: Principal
    facts_json: str
    occurred_at: datetime
    previous_event_hash: str | None
    event_hash: str

    @property
    def facts(self) -> Mapping[str, Any]:
        value = json.loads(self.facts_json)
        if not isinstance(value, Mapping):
            raise ProtocolViolation("event facts must decode to an object")
        return value

    def verify(self) -> None:
        actual = canonical_digest({
            "event_id": self.event_id,
            "transition_id": self.transition_id,
            "transition_hash": self.transition_hash,
            "from_phase": (
                self.from_phase.value
                if self.from_phase is not None
                else None
            ),
            "to_phase": self.to_phase.value,
            "state_version": self.state_version,
            "event_type": self.event_type,
            "actor": {
                "type": self.actor.type,
                "subject": self.actor.subject,
            },
            "facts": self.facts,
            "occurred_at": self.occurred_at.isoformat(),
            "previous_event_hash": self.previous_event_hash,
        })
        if actual != self.event_hash:
            raise ProtocolViolation(
                "transition ledger event digest mismatch"
            )


class SQLiteTransitionLedger:
    """Durable authority lifecycle for StateTransition objects.

    This is not a workflow engine. It stores only control-plane authority
    progress and immutable object bindings. Every mutation is optimistic-CAS
    guarded and emits an append-only hash-chained event.
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
                CREATE TABLE IF NOT EXISTS transition_ledger (
                    transition_id TEXT PRIMARY KEY,
                    transition_hash TEXT NOT NULL UNIQUE,
                    phase TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    facts_json TEXT NOT NULL,
                    parent_transition_hash TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_event_hash TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transition_events (
                    event_id TEXT PRIMARY KEY,
                    transition_id TEXT NOT NULL,
                    transition_hash TEXT NOT NULL,
                    from_phase TEXT,
                    to_phase TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_json TEXT NOT NULL,
                    facts_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    previous_event_hash TEXT,
                    event_hash TEXT NOT NULL UNIQUE,
                    FOREIGN KEY (transition_id)
                        REFERENCES transition_ledger(transition_id)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_transition_events_version
                ON transition_events(transition_id, state_version)
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> TransitionLedgerRecord:
        return TransitionLedgerRecord(
            transition_id=row["transition_id"],
            transition_hash=row["transition_hash"],
            phase=TransitionPhase(row["phase"]),
            state_version=int(row["state_version"]),
            facts_json=row["facts_json"],
            parent_transition_hash=row["parent_transition_hash"],
            created_at=_parse_time(row["created_at"]),
            updated_at=_parse_time(row["updated_at"]),
            last_event_hash=row["last_event_hash"],
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> TransitionLedgerEvent:
        actor_value = json.loads(row["actor_json"])
        if not isinstance(actor_value, Mapping):
            raise ProtocolViolation("ledger actor must decode to an object")
        event = TransitionLedgerEvent(
            event_id=row["event_id"],
            transition_id=row["transition_id"],
            transition_hash=row["transition_hash"],
            from_phase=(
                TransitionPhase(row["from_phase"])
                if row["from_phase"] is not None
                else None
            ),
            to_phase=TransitionPhase(row["to_phase"]),
            state_version=int(row["state_version"]),
            event_type=row["event_type"],
            actor=Principal(
                type=str(actor_value["type"]),
                subject=str(actor_value["subject"]),
            ),
            facts_json=row["facts_json"],
            occurred_at=_parse_time(row["occurred_at"]),
            previous_event_hash=row["previous_event_hash"],
            event_hash=row["event_hash"],
        )
        event.verify()
        return event

    def get(
        self,
        transition_id: str,
    ) -> TransitionLedgerRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM transition_ledger
                WHERE transition_id = ?
                """,
                (transition_id,),
            ).fetchone()
        return self._record(row) if row is not None else None

    def events(
        self,
        transition_id: str,
    ) -> tuple[TransitionLedgerEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM transition_events
                WHERE transition_id = ?
                ORDER BY state_version ASC
                """,
                (transition_id,),
            ).fetchall()
        return tuple(self._event(row) for row in rows)

    def register(
        self,
        *,
        transition: StateTransition,
        action: ActionIntent,
        actor: Principal,
        occurred_at: datetime,
        parent_transition_hash: str | None = None,
    ) -> TransitionLedgerRecord:
        transition.verify()
        action.verify()
        _require_aware(occurred_at, "occurred_at")
        if action.transition_hash != transition.transition_hash:
            raise ProtocolViolation(
                "ledger action is not bound to transition"
            )

        facts = {
            "evidence_hash": transition.evidence_hash,
            "outcome_contract_hash": transition.outcome_contract_hash,
            "action_hash": action.action_hash,
            "resource_uid": transition.subject.resource_uid,
            "expected_generation": transition.expected_generation,
        }
        facts_json = _canonical_json(facts)
        event_id = uuid4().hex
        event_hash = self._event_hash(
            event_id=event_id,
            transition_id=transition.transition_id,
            transition_hash=transition.transition_hash,
            from_phase=None,
            to_phase=TransitionPhase.PROPOSED,
            state_version=1,
            event_type="TRANSITION_REGISTERED",
            actor=actor,
            facts=facts,
            occurred_at=occurred_at,
            previous_event_hash=None,
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT *
                FROM transition_ledger
                WHERE transition_id = ? OR transition_hash = ?
                """,
                (
                    transition.transition_id,
                    transition.transition_hash,
                ),
            ).fetchone()
            if existing is not None:
                record = self._record(existing)
                if (
                    record.transition_id == transition.transition_id
                    and record.transition_hash == transition.transition_hash
                    and record.facts_json == facts_json
                    and record.parent_transition_hash
                    == parent_transition_hash
                ):
                    connection.execute("COMMIT")
                    return record
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "transition logical identity is already bound "
                    "to different immutable content"
                )

            connection.execute(
                """
                INSERT INTO transition_ledger (
                    transition_id,
                    transition_hash,
                    phase,
                    state_version,
                    facts_json,
                    parent_transition_hash,
                    created_at,
                    updated_at,
                    last_event_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transition.transition_id,
                    transition.transition_hash,
                    TransitionPhase.PROPOSED.value,
                    1,
                    facts_json,
                    parent_transition_hash,
                    occurred_at.isoformat(),
                    occurred_at.isoformat(),
                    event_hash,
                ),
            )
            self._insert_event(
                connection,
                event_id=event_id,
                transition_id=transition.transition_id,
                transition_hash=transition.transition_hash,
                from_phase=None,
                to_phase=TransitionPhase.PROPOSED,
                state_version=1,
                event_type="TRANSITION_REGISTERED",
                actor=actor,
                facts_json=facts_json,
                occurred_at=occurred_at,
                previous_event_hash=None,
                event_hash=event_hash,
            )
            connection.execute("COMMIT")

        record = self.get(transition.transition_id)
        if record is None:
            raise ProtocolViolation("registered transition disappeared")
        return record

    def advance(
        self,
        *,
        transition_id: str,
        expected_version: int,
        to_phase: TransitionPhase,
        event_type: str,
        actor: Principal,
        occurred_at: datetime,
        facts: Mapping[str, Any] | None = None,
    ) -> TransitionLedgerRecord:
        _require_aware(occurred_at, "occurred_at")
        if not event_type:
            raise ProtocolViolation("ledger event_type is required")
        new_facts = dict(facts or {})

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM transition_ledger
                WHERE transition_id = ?
                """,
                (transition_id,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "transition is not registered in ledger"
                )
            current = self._record(row)
            if current.state_version != expected_version:
                connection.execute("ROLLBACK")
                raise TransitionLedgerConflict(
                    "transition state_version changed"
                )
            allowed = _ALLOWED[current.phase]
            if to_phase not in allowed:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    f"illegal transition phase change: "
                    f"{current.phase.value} -> {to_phase.value}"
                )

            merged = dict(current.facts)
            for key, value in new_facts.items():
                if key in merged and merged[key] != value:
                    connection.execute("ROLLBACK")
                    raise ProtocolViolation(
                        f"immutable ledger fact changed: {key}"
                    )
                merged[key] = value
            merged_json = _canonical_json(merged)
            event_facts_json = _canonical_json(new_facts)
            next_version = current.state_version + 1
            event_id = uuid4().hex
            event_hash = self._event_hash(
                event_id=event_id,
                transition_id=current.transition_id,
                transition_hash=current.transition_hash,
                from_phase=current.phase,
                to_phase=to_phase,
                state_version=next_version,
                event_type=event_type,
                actor=actor,
                facts=new_facts,
                occurred_at=occurred_at,
                previous_event_hash=current.last_event_hash,
            )

            updated = connection.execute(
                """
                UPDATE transition_ledger
                SET phase = ?,
                    state_version = ?,
                    facts_json = ?,
                    updated_at = ?,
                    last_event_hash = ?
                WHERE transition_id = ?
                  AND state_version = ?
                  AND phase = ?
                """,
                (
                    to_phase.value,
                    next_version,
                    merged_json,
                    occurred_at.isoformat(),
                    event_hash,
                    current.transition_id,
                    expected_version,
                    current.phase.value,
                ),
            )
            if updated.rowcount != 1:
                connection.execute("ROLLBACK")
                raise TransitionLedgerConflict(
                    "transition state changed during CAS update"
                )

            self._insert_event(
                connection,
                event_id=event_id,
                transition_id=current.transition_id,
                transition_hash=current.transition_hash,
                from_phase=current.phase,
                to_phase=to_phase,
                state_version=next_version,
                event_type=event_type,
                actor=actor,
                facts_json=event_facts_json,
                occurred_at=occurred_at,
                previous_event_hash=current.last_event_hash,
                event_hash=event_hash,
            )
            connection.execute("COMMIT")

        record = self.get(transition_id)
        if record is None:
            raise ProtocolViolation("advanced transition disappeared")
        return record

    def record_policy(
        self,
        *,
        transition_id: str,
        expected_version: int,
        policy_input: TransitionPolicyInput,
        decision: PolicyDecisionRecord,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        policy_input.verify()
        decision.verify()
        record = self._require(transition_id)
        if policy_input.transition_hash != record.transition_hash:
            raise ProtocolViolation(
                "ledger policy input transition mismatch"
            )
        if policy_input.action_hash != record.facts["action_hash"]:
            raise ProtocolViolation(
                "ledger policy input action mismatch"
            )
        if policy_input.evidence_hash != record.facts["evidence_hash"]:
            raise ProtocolViolation(
                "ledger policy input evidence mismatch"
            )
        if decision.input_hash != policy_input.input_hash:
            raise ProtocolViolation(
                "ledger policy decision input mismatch"
            )
        if decision.policy_version != policy_input.policy_version:
            raise ProtocolViolation(
                "ledger policy version mismatch"
            )

        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=TransitionPhase.POLICY_EVALUATED,
            event_type="POLICY_EVALUATED",
            actor=actor,
            occurred_at=occurred_at,
            facts={
                "policy_version": policy_input.policy_version,
                "policy_input_hash": policy_input.input_hash,
                "policy_decision_hash": decision.decision_hash,
                "policy_effect": decision.effect.value,
            },
        )

    def route_policy_result(
        self,
        *,
        transition_id: str,
        expected_version: int,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        record = self._require(transition_id)
        effect = record.facts.get("policy_effect")
        if effect == PolicyEffect.PERMIT.value:
            target = TransitionPhase.AWAITING_APPROVAL
            event_type = "APPROVAL_REQUIRED"
        elif effect == PolicyEffect.DENY.value:
            target = TransitionPhase.DENIED
            event_type = "POLICY_DENIED"
        else:
            raise ProtocolViolation(
                "ledger policy effect is missing or invalid"
            )
        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=target,
            event_type=event_type,
            actor=actor,
            occurred_at=occurred_at,
        )

    def record_authorization(
        self,
        *,
        transition_id: str,
        expected_version: int,
        approval: TransitionApproval,
        authorization: AuthorizationBinding,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        approval.verify()
        authorization.verify()
        record = self._require(transition_id)

        checks = {
            "transition_hash": (
                approval.transition_hash,
                record.transition_hash,
            ),
            "action_hash": (
                approval.action_hash,
                record.facts["action_hash"],
            ),
            "evidence_hash": (
                approval.evidence_hash,
                record.facts["evidence_hash"],
            ),
            "policy_input_hash": (
                approval.policy_input_hash,
                record.facts["policy_input_hash"],
            ),
            "policy_decision_hash": (
                approval.policy_decision_hash,
                record.facts["policy_decision_hash"],
            ),
            "authorization_transition": (
                authorization.transition_hash,
                record.transition_hash,
            ),
            "authorization_action": (
                authorization.action_hash,
                record.facts["action_hash"],
            ),
            "authorization_evidence": (
                authorization.evidence_hash,
                record.facts["evidence_hash"],
            ),
            "authorization_policy_decision": (
                authorization.policy_decision_hash,
                record.facts["policy_decision_hash"],
            ),
            "authorization_approval": (
                authorization.approval_hash,
                approval.approval_hash,
            ),
        }
        for name, (actual, expected) in checks.items():
            if actual != expected:
                raise ProtocolViolation(
                    f"ledger authorization binding mismatch: {name}"
                )

        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=TransitionPhase.AUTHORIZED,
            event_type="TRANSITION_AUTHORIZED",
            actor=actor,
            occurred_at=occurred_at,
            facts={
                "approval_hash": approval.approval_hash,
                "approver_type": approval.approver.type,
                "approver_subject": approval.approver.subject,
                "authorization_hash": authorization.authorization_hash,
            },
        )

    def record_lease(
        self,
        *,
        transition_id: str,
        expected_version: int,
        lease: ExecutionLease,
        fence: ExecutionFence,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        record = self._require(transition_id)
        if fence.transition_hash != record.transition_hash:
            raise ProtocolViolation(
                "ledger fence transition mismatch"
            )
        if fence.action_hash != record.facts["action_hash"]:
            raise ProtocolViolation(
                "ledger fence action mismatch"
            )
        if (
            fence.authorization_hash
            != record.facts["authorization_hash"]
        ):
            raise ProtocolViolation(
                "ledger fence authorization mismatch"
            )
        if fence.resource_uid != record.facts["resource_uid"]:
            raise ProtocolViolation(
                "ledger fence resource mismatch"
            )
        if lease.resource_uid != fence.resource_uid:
            raise ProtocolViolation(
                "ledger lease resource mismatch"
            )
        if (
            lease.lease_id != fence.lease_id
            or lease.epoch != fence.lease_epoch
            or lease.holder != fence.lease_holder
        ):
            raise ProtocolViolation(
                "ledger lease/fence ownership mismatch"
            )

        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=TransitionPhase.LEASED,
            event_type="EXECUTION_LEASED",
            actor=actor,
            occurred_at=occurred_at,
            facts={
                "lease_id": lease.lease_id,
                "lease_epoch": lease.epoch,
                "lease_holder_type": lease.holder.type,
                "lease_holder_subject": lease.holder.subject,
            },
        )

    def record_execution_prepared(
        self,
        *,
        transition_id: str,
        expected_version: int,
        attempt: ExecutionAttempt,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        attempt.verify()
        record = self._require(transition_id)
        if attempt.state is not ExecutionAttemptState.PREPARED:
            raise ProtocolViolation(
                "ledger requires PREPARED execution attempt"
            )
        checks = (
            (attempt.transition_hash, record.transition_hash),
            (attempt.action_hash, record.facts["action_hash"]),
            (
                attempt.authorization_hash,
                record.facts["authorization_hash"],
            ),
            (attempt.lease_id, record.facts["lease_id"]),
            (attempt.lease_epoch, record.facts["lease_epoch"]),
        )
        if any(actual != expected for actual, expected in checks):
            raise ProtocolViolation(
                "ledger execution attempt binding mismatch"
            )

        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=TransitionPhase.EXECUTION_PREPARED,
            event_type="EXECUTION_PREPARED",
            actor=actor,
            occurred_at=occurred_at,
            facts={
                "execution_attempt_id": attempt.attempt_id,
                "execution_attempt_hash": attempt.attempt_hash,
                "operation_id": attempt.operation_id,
            },
        )

    def start_reconcile(
        self,
        *,
        transition_id: str,
        expected_version: int,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=TransitionPhase.RECONCILING,
            event_type="EXECUTION_RECONCILING",
            actor=actor,
            occurred_at=occurred_at,
        )

    def record_execution_result(
        self,
        *,
        transition_id: str,
        expected_version: int,
        attempt: ExecutionAttempt,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        attempt.verify()
        record = self._require(transition_id)
        if attempt.attempt_id != record.facts["execution_attempt_id"]:
            raise ProtocolViolation(
                "ledger execution result attempt mismatch"
            )
        if attempt.attempt_hash != record.facts["execution_attempt_hash"]:
            raise ProtocolViolation(
                "ledger execution result digest mismatch"
            )

        if attempt.state is ExecutionAttemptState.COMMITTED:
            target = TransitionPhase.EXECUTED
            event_type = "EXECUTION_COMMITTED"
        elif attempt.state is ExecutionAttemptState.ABORTED:
            target = TransitionPhase.EXECUTION_NOT_APPLIED
            event_type = "EXECUTION_NOT_APPLIED"
        elif attempt.state is ExecutionAttemptState.UNKNOWN:
            target = TransitionPhase.FROZEN
            event_type = "EXECUTION_UNKNOWN"
        else:
            raise ProtocolViolation(
                "execution attempt is not terminal"
            )

        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=target,
            event_type=event_type,
            actor=actor,
            occurred_at=occurred_at,
            facts={
                "execution_terminal_state": attempt.state.value,
                "execution_result_hash": attempt.result_hash,
            },
        )

    def start_verification(
        self,
        *,
        transition_id: str,
        expected_version: int,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=TransitionPhase.VERIFYING,
            event_type="VERIFICATION_STARTED",
            actor=actor,
            occurred_at=occurred_at,
        )

    def record_verification(
        self,
        *,
        transition_id: str,
        expected_version: int,
        result: OutcomeVerificationResult,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        record = self._require(transition_id)
        if result.transition_hash != record.transition_hash:
            raise ProtocolViolation(
                "ledger verification transition mismatch"
            )
        if (
            result.outcome_contract_hash
            != record.facts["outcome_contract_hash"]
        ):
            raise ProtocolViolation(
                "ledger verification contract mismatch"
            )

        if result.status is VerificationStatus.SUCCEEDED:
            target = TransitionPhase.SUCCEEDED
        elif result.status in {
            VerificationStatus.DEGRADED,
            VerificationStatus.TIMED_OUT,
            VerificationStatus.INVARIANT_VIOLATION,
        }:
            target = TransitionPhase.RECOVERY_REQUIRED
        elif result.status is VerificationStatus.UNKNOWN:
            target = TransitionPhase.FROZEN
        else:
            raise ProtocolViolation(
                "unsupported verification status"
            )

        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=target,
            event_type=f"VERIFICATION_{result.status.value}",
            actor=actor,
            occurred_at=occurred_at,
            facts={
                "verification_status": result.status.value,
                "verification_observation_hash": result.observation_hash,
            },
        )

    def release(
        self,
        *,
        transition_id: str,
        expected_version: int,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=TransitionPhase.RELEASED,
            event_type="TRANSITION_RELEASED",
            actor=actor,
            occurred_at=occurred_at,
        )

    def start_recovery(
        self,
        *,
        transition_id: str,
        expected_version: int,
        recovery_transition_hash: str,
        actor: Principal,
        occurred_at: datetime,
    ) -> TransitionLedgerRecord:
        if not recovery_transition_hash:
            raise ProtocolViolation(
                "recovery transition hash is required"
            )
        return self.advance(
            transition_id=transition_id,
            expected_version=expected_version,
            to_phase=TransitionPhase.RECOVERING,
            event_type="RECOVERY_STARTED",
            actor=actor,
            occurred_at=occurred_at,
            facts={
                "recovery_transition_hash": recovery_transition_hash,
            },
        )

    def verify_history(
        self,
        transition_id: str,
    ) -> TransitionLedgerRecord:
        record = self._require(transition_id)
        events = self.events(transition_id)
        if not events:
            raise ProtocolViolation(
                "transition ledger history is empty"
            )

        previous_hash: str | None = None
        phase: TransitionPhase | None = None
        version = 0
        facts: dict[str, Any] = {}

        for event in events:
            event.verify()
            if event.transition_hash != record.transition_hash:
                raise ProtocolViolation(
                    "ledger event transition hash mismatch"
                )
            if event.previous_event_hash != previous_hash:
                raise ProtocolViolation(
                    "transition ledger event chain is broken"
                )
            if event.state_version != version + 1:
                raise ProtocolViolation(
                    "transition ledger event version is not contiguous"
                )
            if event.from_phase != phase:
                raise ProtocolViolation(
                    "transition ledger event phase chain is broken"
                )
            if phase is not None and event.to_phase not in _ALLOWED[phase]:
                raise ProtocolViolation(
                    "transition ledger history contains illegal phase change"
                )
            for key, value in event.facts.items():
                if key in facts and facts[key] != value:
                    raise ProtocolViolation(
                        f"transition ledger history rewrites fact: {key}"
                    )
                facts[key] = value
            phase = event.to_phase
            version = event.state_version
            previous_hash = event.event_hash

        if phase != record.phase:
            raise ProtocolViolation(
                "transition ledger materialized phase diverged from history"
            )
        if version != record.state_version:
            raise ProtocolViolation(
                "transition ledger state_version diverged from history"
            )
        if previous_hash != record.last_event_hash:
            raise ProtocolViolation(
                "transition ledger last event hash diverged from history"
            )
        if _canonical_json(facts) != record.facts_json:
            raise ProtocolViolation(
                "transition ledger materialized facts diverged from history"
            )
        return record

    def _require(self, transition_id: str) -> TransitionLedgerRecord:
        record = self.get(transition_id)
        if record is None:
            raise ProtocolViolation(
                "transition is not registered in ledger"
            )
        return record

    @staticmethod
    def _event_hash(
        *,
        event_id: str,
        transition_id: str,
        transition_hash: str,
        from_phase: TransitionPhase | None,
        to_phase: TransitionPhase,
        state_version: int,
        event_type: str,
        actor: Principal,
        facts: Mapping[str, Any],
        occurred_at: datetime,
        previous_event_hash: str | None,
    ) -> str:
        return canonical_digest({
            "event_id": event_id,
            "transition_id": transition_id,
            "transition_hash": transition_hash,
            "from_phase": (
                from_phase.value
                if from_phase is not None
                else None
            ),
            "to_phase": to_phase.value,
            "state_version": state_version,
            "event_type": event_type,
            "actor": {
                "type": actor.type,
                "subject": actor.subject,
            },
            "facts": dict(facts),
            "occurred_at": occurred_at.isoformat(),
            "previous_event_hash": previous_event_hash,
        })

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        event_id: str,
        transition_id: str,
        transition_hash: str,
        from_phase: TransitionPhase | None,
        to_phase: TransitionPhase,
        state_version: int,
        event_type: str,
        actor: Principal,
        facts_json: str,
        occurred_at: datetime,
        previous_event_hash: str | None,
        event_hash: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO transition_events (
                event_id,
                transition_id,
                transition_hash,
                from_phase,
                to_phase,
                state_version,
                event_type,
                actor_json,
                facts_json,
                occurred_at,
                previous_event_hash,
                event_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                transition_id,
                transition_hash,
                (
                    from_phase.value
                    if from_phase is not None
                    else None
                ),
                to_phase.value,
                state_version,
                event_type,
                _actor_json(actor),
                facts_json,
                occurred_at.isoformat(),
                previous_event_hash,
                event_hash,
            ),
        )
