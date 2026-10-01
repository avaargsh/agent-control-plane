from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .state_transition_protocol import (
    Principal,
    ProtocolViolation,
    canonical_digest,
)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


def _snapshot_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolViolation(
            "work context payload must be canonical JSON"
        ) from exc
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ProtocolViolation("work context state must be an object")
    return decoded


def _canonical_strings(
    values: Sequence[str],
    *,
    field_name: str,
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ProtocolViolation(
                f"{field_name} entries must be non-empty strings"
            )
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _merge_unique(
    current: tuple[str, ...],
    additions: Sequence[str],
    *,
    field_name: str,
) -> tuple[str, ...]:
    return _canonical_strings(
        (*current, *additions),
        field_name=field_name,
    )


class WorkStatus(str, Enum):
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    DONE = "DONE"


@dataclass(frozen=True)
class WorkSnapshot:
    work_id: str
    namespace: str
    goal: str
    version: int
    status: WorkStatus
    owner: Principal | None
    state: Mapping[str, Any]
    decisions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    updated_at: datetime
    snapshot_hash: str
    snapshot_version: str = "work-snapshot/v1"

    @classmethod
    def seal(
        cls,
        *,
        work_id: str,
        namespace: str,
        goal: str,
        version: int,
        status: WorkStatus,
        owner: Principal | None,
        state: Mapping[str, Any],
        decisions: Sequence[str] = (),
        evidence_refs: Sequence[str] = (),
        updated_at: datetime,
    ) -> "WorkSnapshot":
        if not work_id or not namespace or not goal:
            raise ProtocolViolation(
                "work_id, namespace and goal are required"
            )
        if version <= 0:
            raise ProtocolViolation("work version must be positive")
        _require_aware(updated_at, "updated_at")
        state_snapshot = _snapshot_mapping(state)
        canonical_decisions = _canonical_strings(
            decisions,
            field_name="decisions",
        )
        canonical_evidence = _canonical_strings(
            evidence_refs,
            field_name="evidence_refs",
        )
        provisional = cls(
            work_id=work_id,
            namespace=namespace,
            goal=goal,
            version=version,
            status=status,
            owner=owner,
            state=state_snapshot,
            decisions=canonical_decisions,
            evidence_refs=canonical_evidence,
            updated_at=updated_at,
            snapshot_hash="",
        )
        return cls(
            work_id=work_id,
            namespace=namespace,
            goal=goal,
            version=version,
            status=status,
            owner=owner,
            state=state_snapshot,
            decisions=canonical_decisions,
            evidence_refs=canonical_evidence,
            updated_at=updated_at,
            snapshot_hash=canonical_digest(
                provisional,
                exclude=("snapshot_hash",),
            ),
        )

    def verify(self) -> None:
        if self.version <= 0:
            raise ProtocolViolation("work version must be positive")
        if not isinstance(self.status, WorkStatus):
            raise ProtocolViolation("work status must be a WorkStatus")
        if self.owner is not None and not isinstance(self.owner, Principal):
            raise ProtocolViolation("work owner must be a Principal")
        _require_aware(self.updated_at, "updated_at")
        _snapshot_mapping(self.state)
        _canonical_strings(self.decisions, field_name="decisions")
        _canonical_strings(
            self.evidence_refs,
            field_name="evidence_refs",
        )
        actual = canonical_digest(
            self,
            exclude=("snapshot_hash",),
        )
        if actual != self.snapshot_hash:
            raise ProtocolViolation(
                "work snapshot digest mismatch: "
                f"expected {self.snapshot_hash}, got {actual}"
            )


@dataclass(frozen=True)
class WorkEvent:
    event_id: str
    work_id: str
    from_version: int
    to_version: int
    actor: Principal
    operation: str
    payload: Mapping[str, Any]
    prior_snapshot_hash: str
    snapshot_hash: str
    created_at: datetime
    event_hash: str
    event_version: str = "work-event/v1"

    @classmethod
    def seal(
        cls,
        *,
        event_id: str,
        work_id: str,
        from_version: int,
        to_version: int,
        actor: Principal,
        operation: str,
        payload: Mapping[str, Any],
        prior_snapshot_hash: str,
        snapshot_hash: str,
        created_at: datetime,
    ) -> "WorkEvent":
        if not event_id or not work_id or not operation:
            raise ProtocolViolation(
                "event_id, work_id and operation are required"
            )
        if from_version < 0 or to_version != from_version + 1:
            raise ProtocolViolation(
                "work event versions must advance by exactly one"
            )
        if not prior_snapshot_hash or not snapshot_hash:
            raise ProtocolViolation(
                "work event snapshot hashes are required"
            )
        _require_aware(created_at, "created_at")
        payload_snapshot = _snapshot_mapping(payload)
        provisional = cls(
            event_id=event_id,
            work_id=work_id,
            from_version=from_version,
            to_version=to_version,
            actor=actor,
            operation=operation,
            payload=payload_snapshot,
            prior_snapshot_hash=prior_snapshot_hash,
            snapshot_hash=snapshot_hash,
            created_at=created_at,
            event_hash="",
        )
        return cls(
            event_id=event_id,
            work_id=work_id,
            from_version=from_version,
            to_version=to_version,
            actor=actor,
            operation=operation,
            payload=payload_snapshot,
            prior_snapshot_hash=prior_snapshot_hash,
            snapshot_hash=snapshot_hash,
            created_at=created_at,
            event_hash=canonical_digest(
                provisional,
                exclude=("event_hash",),
            ),
        )

    def verify(self) -> None:
        if self.from_version < 0 or self.to_version != self.from_version + 1:
            raise ProtocolViolation(
                "work event versions must advance by exactly one"
            )
        if not isinstance(self.actor, Principal):
            raise ProtocolViolation("work event actor must be a Principal")
        _require_aware(self.created_at, "created_at")
        _snapshot_mapping(self.payload)
        actual = canonical_digest(
            self,
            exclude=("event_hash",),
        )
        if actual != self.event_hash:
            raise ProtocolViolation(
                "work event digest mismatch: "
                f"expected {self.event_hash}, got {actual}"
            )


def _verify_event_tail(
    *,
    work: WorkSnapshot,
    events: Sequence[WorkEvent],
) -> None:
    """Verify a contiguous event suffix ending at the supplied snapshot."""

    previous: WorkEvent | None = None
    for event in events:
        event.verify()
        if event.work_id != work.work_id:
            raise ProtocolViolation(
                "work event belongs to another work item"
            )
        if event.to_version > work.version:
            raise ProtocolViolation(
                "work event is newer than the current snapshot"
            )
        if previous is not None:
            if event.from_version != previous.to_version:
                raise ProtocolViolation(
                    "work event versions are not contiguous"
                )
            if event.prior_snapshot_hash != previous.snapshot_hash:
                raise ProtocolViolation(
                    "work event snapshot hash chain is broken"
                )
        elif (
            event.from_version == 0
            and event.prior_snapshot_hash != "GENESIS"
        ):
            raise ProtocolViolation(
                "genesis work event has invalid prior snapshot hash"
            )
        previous = event

    if not events:
        return

    tail = events[-1]
    if tail.to_version != work.version:
        raise ProtocolViolation(
            "work event tail does not reach current snapshot version"
        )
    if tail.snapshot_hash != work.snapshot_hash:
        raise ProtocolViolation(
            "work event tail snapshot hash does not match current snapshot"
        )


@dataclass(frozen=True)
class ContextProjection:
    consumer: Principal
    work: WorkSnapshot
    recent_events: tuple[WorkEvent, ...]
    projection_hash: str
    projection_version: str = "context-projection/v1"

    @classmethod
    def seal(
        cls,
        *,
        consumer: Principal,
        work: WorkSnapshot,
        recent_events: Sequence[WorkEvent],
    ) -> "ContextProjection":
        work.verify()
        events = tuple(recent_events)
        _verify_event_tail(
            work=work,
            events=events,
        )
        provisional = cls(
            consumer=consumer,
            work=work,
            recent_events=events,
            projection_hash="",
        )
        return cls(
            consumer=consumer,
            work=work,
            recent_events=events,
            projection_hash=canonical_digest(
                provisional,
                exclude=("projection_hash",),
            ),
        )

    def verify(self) -> None:
        self.work.verify()
        _verify_event_tail(
            work=self.work,
            events=self.recent_events,
        )
        actual = canonical_digest(
            self,
            exclude=("projection_hash",),
        )
        if actual != self.projection_hash:
            raise ProtocolViolation("context projection digest mismatch")


class SQLiteWorkContextStore:
    """Authoritative cross-agent work state with optimistic concurrency.

    This store intentionally does not implement semantic memory. It owns the
    current work snapshot, version/CAS semantics and an append-only mutation
    history. Long-term memory, vector retrieval and agent-specific prompt
    compaction remain separate context sources.
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_snapshots (
                    work_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version > 0),
                    status TEXT NOT NULL,
                    owner_type TEXT,
                    owner_subject TEXT,
                    state_json TEXT NOT NULL,
                    decisions_json TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_events (
                    event_id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    from_version INTEGER NOT NULL,
                    to_version INTEGER NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_subject TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    prior_snapshot_hash TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    UNIQUE(work_id, to_version),
                    FOREIGN KEY(work_id) REFERENCES work_snapshots(work_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_work_events_work_version
                ON work_events(work_id, to_version)
                """
            )

    @staticmethod
    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        _require_aware(parsed, "stored work timestamp")
        return parsed

    @classmethod
    def _snapshot_from_row(cls, row: sqlite3.Row) -> WorkSnapshot:
        owner = None
        if row["owner_type"] is not None or row["owner_subject"] is not None:
            if row["owner_type"] is None or row["owner_subject"] is None:
                raise ProtocolViolation(
                    "stored work owner is only partially populated"
                )
            owner = Principal(
                type=row["owner_type"],
                subject=row["owner_subject"],
            )
        snapshot = WorkSnapshot(
            work_id=row["work_id"],
            namespace=row["namespace"],
            goal=row["goal"],
            version=int(row["version"]),
            status=WorkStatus(row["status"]),
            owner=owner,
            state=json.loads(row["state_json"]),
            decisions=tuple(json.loads(row["decisions_json"])),
            evidence_refs=tuple(json.loads(row["evidence_refs_json"])),
            updated_at=cls._parse_time(row["updated_at"]),
            snapshot_hash=row["snapshot_hash"],
        )
        snapshot.verify()
        return snapshot

    @classmethod
    def _event_from_row(cls, row: sqlite3.Row) -> WorkEvent:
        event = WorkEvent(
            event_id=row["event_id"],
            work_id=row["work_id"],
            from_version=int(row["from_version"]),
            to_version=int(row["to_version"]),
            actor=Principal(
                type=row["actor_type"],
                subject=row["actor_subject"],
            ),
            operation=row["operation"],
            payload=json.loads(row["payload_json"]),
            prior_snapshot_hash=row["prior_snapshot_hash"],
            snapshot_hash=row["snapshot_hash"],
            created_at=cls._parse_time(row["created_at"]),
            event_hash=row["event_hash"],
        )
        event.verify()
        return event

    @staticmethod
    def _write_snapshot(
        connection: sqlite3.Connection,
        snapshot: WorkSnapshot,
        *,
        expected_version: int | None,
    ) -> None:
        owner_type = snapshot.owner.type if snapshot.owner else None
        owner_subject = snapshot.owner.subject if snapshot.owner else None
        values = (
            snapshot.namespace,
            snapshot.goal,
            snapshot.version,
            snapshot.status.value,
            owner_type,
            owner_subject,
            json.dumps(
                snapshot.state,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
            json.dumps(snapshot.decisions, ensure_ascii=False),
            json.dumps(snapshot.evidence_refs, ensure_ascii=False),
            snapshot.updated_at.isoformat(),
            snapshot.snapshot_hash,
            snapshot.work_id,
        )
        if expected_version is None:
            connection.execute(
                """
                INSERT INTO work_snapshots (
                    namespace,
                    goal,
                    version,
                    status,
                    owner_type,
                    owner_subject,
                    state_json,
                    decisions_json,
                    evidence_refs_json,
                    updated_at,
                    snapshot_hash,
                    work_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            return

        cursor = connection.execute(
            """
            UPDATE work_snapshots
            SET namespace = ?,
                goal = ?,
                version = ?,
                status = ?,
                owner_type = ?,
                owner_subject = ?,
                state_json = ?,
                decisions_json = ?,
                evidence_refs_json = ?,
                updated_at = ?,
                snapshot_hash = ?
            WHERE work_id = ? AND version = ?
            """,
            (*values, expected_version),
        )
        if cursor.rowcount != 1:
            raise ProtocolViolation(
                "work snapshot compare-and-swap failed"
            )

    @staticmethod
    def _write_event(
        connection: sqlite3.Connection,
        event: WorkEvent,
    ) -> None:
        connection.execute(
            """
            INSERT INTO work_events (
                event_id,
                work_id,
                from_version,
                to_version,
                actor_type,
                actor_subject,
                operation,
                payload_json,
                prior_snapshot_hash,
                snapshot_hash,
                created_at,
                event_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.work_id,
                event.from_version,
                event.to_version,
                event.actor.type,
                event.actor.subject,
                event.operation,
                json.dumps(
                    event.payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                event.prior_snapshot_hash,
                event.snapshot_hash,
                event.created_at.isoformat(),
                event.event_hash,
            ),
        )

    def create(
        self,
        *,
        work_id: str,
        namespace: str,
        goal: str,
        actor: Principal,
        created_at: datetime,
        state: Mapping[str, Any] | None = None,
    ) -> WorkSnapshot:
        snapshot = WorkSnapshot.seal(
            work_id=work_id,
            namespace=namespace,
            goal=goal,
            version=1,
            status=WorkStatus.NEW,
            owner=None,
            state=state or {},
            updated_at=created_at,
        )
        event = WorkEvent.seal(
            event_id=uuid4().hex,
            work_id=work_id,
            from_version=0,
            to_version=1,
            actor=actor,
            operation="create",
            payload={
                "namespace": namespace,
                "goal": goal,
            },
            prior_snapshot_hash="GENESIS",
            snapshot_hash=snapshot.snapshot_hash,
            created_at=created_at,
        )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._write_snapshot(
                    connection,
                    snapshot,
                    expected_version=None,
                )
                self._write_event(connection, event)
                connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            raise ProtocolViolation(
                f"work item already exists: {work_id}"
            ) from exc
        return snapshot

    @classmethod
    def _read_snapshot(
        cls,
        connection: sqlite3.Connection,
        work_id: str,
    ) -> WorkSnapshot:
        row = connection.execute(
            """
            SELECT *
            FROM work_snapshots
            WHERE work_id = ?
            """,
            (work_id,),
        ).fetchone()
        if row is None:
            raise ProtocolViolation(
                f"work item does not exist: {work_id}"
            )
        return cls._snapshot_from_row(row)

    def get(self, work_id: str) -> WorkSnapshot:
        with self._connect() as connection:
            return self._read_snapshot(connection, work_id)

    def changes_since_with_snapshot(
        self,
        work_id: str,
        version: int,
    ) -> tuple[WorkSnapshot, tuple[WorkEvent, ...]]:
        """Read the current snapshot and event suffix from one DB snapshot."""

        if version < 0:
            raise ProtocolViolation("version cannot be negative")

        with self._connect() as connection:
            connection.execute("BEGIN")
            work = self._read_snapshot(connection, work_id)
            if version > work.version:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "requested version is newer than current work version"
                )
            rows = connection.execute(
                """
                SELECT *
                FROM work_events
                WHERE work_id = ? AND to_version > ?
                ORDER BY to_version ASC
                """,
                (work_id, version),
            ).fetchall()
            connection.execute("COMMIT")

        events = tuple(self._event_from_row(row) for row in rows)
        if version == work.version:
            if events:
                raise ProtocolViolation(
                    "work event history contains events past current version"
                )
            return work, ()

        if not events or events[0].from_version != version:
            raise ProtocolViolation(
                "work event history does not continue from requested version"
            )
        _verify_event_tail(
            work=work,
            events=events,
        )
        return work, events

    def changes_since(
        self,
        work_id: str,
        version: int,
    ) -> tuple[WorkEvent, ...]:
        _, events = self.changes_since_with_snapshot(
            work_id,
            version,
        )
        return events

    def _commit(
        self,
        *,
        work_id: str,
        expected_version: int,
        actor: Principal,
        operation: str,
        updated_at: datetime,
        new_status: WorkStatus | None = None,
        new_owner: Principal | None = None,
        replace_owner: bool = False,
        state_patch: Mapping[str, Any] | None = None,
        decisions: Sequence[str] = (),
        evidence_refs: Sequence[str] = (),
        payload_extra: Mapping[str, Any] | None = None,
    ) -> WorkSnapshot:
        _require_aware(updated_at, "updated_at")
        if expected_version <= 0:
            raise ProtocolViolation(
                "expected work version must be positive"
            )
        state_patch_snapshot = _snapshot_mapping(state_patch or {})
        canonical_decisions = _canonical_strings(
            decisions,
            field_name="decisions",
        )
        canonical_evidence = _canonical_strings(
            evidence_refs,
            field_name="evidence_refs",
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM work_snapshots
                WHERE work_id = ?
                """,
                (work_id,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    f"work item does not exist: {work_id}"
                )
            current = self._snapshot_from_row(row)
            if current.version != expected_version:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "stale work version: "
                    f"expected {expected_version}, current {current.version}"
                )

            next_state = dict(current.state)
            next_state.update(state_patch_snapshot)
            owner = new_owner if replace_owner else current.owner
            status = new_status or current.status
            next_snapshot = WorkSnapshot.seal(
                work_id=current.work_id,
                namespace=current.namespace,
                goal=current.goal,
                version=current.version + 1,
                status=status,
                owner=owner,
                state=next_state,
                decisions=_merge_unique(
                    current.decisions,
                    canonical_decisions,
                    field_name="decisions",
                ),
                evidence_refs=_merge_unique(
                    current.evidence_refs,
                    canonical_evidence,
                    field_name="evidence_refs",
                ),
                updated_at=updated_at,
            )

            payload: dict[str, Any] = {
                "state_patch": state_patch_snapshot,
                "decisions": list(canonical_decisions),
                "evidence_refs": list(canonical_evidence),
                "status": status.value,
                "owner": (
                    {
                        "type": owner.type,
                        "subject": owner.subject,
                    }
                    if owner is not None
                    else None
                ),
            }
            if payload_extra:
                payload.update(_snapshot_mapping(payload_extra))

            event = WorkEvent.seal(
                event_id=uuid4().hex,
                work_id=work_id,
                from_version=current.version,
                to_version=next_snapshot.version,
                actor=actor,
                operation=operation,
                payload=payload,
                prior_snapshot_hash=current.snapshot_hash,
                snapshot_hash=next_snapshot.snapshot_hash,
                created_at=updated_at,
            )

            self._write_snapshot(
                connection,
                next_snapshot,
                expected_version=current.version,
            )
            self._write_event(connection, event)
            connection.execute("COMMIT")
        return next_snapshot

    def claim(
        self,
        *,
        work_id: str,
        expected_version: int,
        agent: Principal,
        claimed_at: datetime,
    ) -> WorkSnapshot:
        current = self.get(work_id)
        if current.version != expected_version:
            raise ProtocolViolation(
                "stale work version: "
                f"expected {expected_version}, current {current.version}"
            )
        if current.status is WorkStatus.DONE:
            raise ProtocolViolation("completed work cannot be claimed")
        if current.owner is not None:
            raise ProtocolViolation(
                "work is already owned by "
                f"{current.owner.type}:{current.owner.subject}"
            )
        return self._commit(
            work_id=work_id,
            expected_version=expected_version,
            actor=agent,
            operation="claim",
            updated_at=claimed_at,
            new_status=WorkStatus.ACTIVE,
            new_owner=agent,
            replace_owner=True,
        )

    def record_progress(
        self,
        *,
        work_id: str,
        expected_version: int,
        actor: Principal,
        updated_at: datetime,
        state_patch: Mapping[str, Any] | None = None,
        decisions: Sequence[str] = (),
        evidence_refs: Sequence[str] = (),
        status: WorkStatus | None = None,
    ) -> WorkSnapshot:
        current = self.get(work_id)
        if current.version != expected_version:
            raise ProtocolViolation(
                "stale work version: "
                f"expected {expected_version}, current {current.version}"
            )
        if current.owner != actor:
            raise ProtocolViolation(
                "only the current work owner may record progress"
            )
        if current.status is WorkStatus.DONE:
            raise ProtocolViolation("completed work is immutable")
        if (
            not state_patch
            and not decisions
            and not evidence_refs
            and status is None
        ):
            raise ProtocolViolation("progress update cannot be empty")
        if status is WorkStatus.NEW:
            raise ProtocolViolation(
                "active work cannot transition back to NEW"
            )
        return self._commit(
            work_id=work_id,
            expected_version=expected_version,
            actor=actor,
            operation="progress",
            updated_at=updated_at,
            new_status=status,
            state_patch=state_patch,
            decisions=decisions,
            evidence_refs=evidence_refs,
        )

    def handoff(
        self,
        *,
        work_id: str,
        expected_version: int,
        from_agent: Principal,
        to_agent: Principal,
        handed_off_at: datetime,
        reason: str,
        state_patch: Mapping[str, Any] | None = None,
        evidence_refs: Sequence[str] = (),
    ) -> WorkSnapshot:
        if from_agent == to_agent:
            raise ProtocolViolation(
                "handoff target must differ from current owner"
            )
        if not reason.strip():
            raise ProtocolViolation("handoff reason is required")
        current = self.get(work_id)
        if current.version != expected_version:
            raise ProtocolViolation(
                "stale work version: "
                f"expected {expected_version}, current {current.version}"
            )
        if current.owner != from_agent:
            raise ProtocolViolation(
                "handoff source does not own the work"
            )
        if current.status is WorkStatus.DONE:
            raise ProtocolViolation("completed work cannot be handed off")
        return self._commit(
            work_id=work_id,
            expected_version=expected_version,
            actor=from_agent,
            operation="handoff",
            updated_at=handed_off_at,
            new_status=WorkStatus.ACTIVE,
            new_owner=to_agent,
            replace_owner=True,
            state_patch=state_patch,
            evidence_refs=evidence_refs,
            payload_extra={
                "handoff_from": {
                    "type": from_agent.type,
                    "subject": from_agent.subject,
                },
                "handoff_to": {
                    "type": to_agent.type,
                    "subject": to_agent.subject,
                },
                "reason": reason,
            },
        )

    def complete(
        self,
        *,
        work_id: str,
        expected_version: int,
        agent: Principal,
        completed_at: datetime,
        evidence_refs: Sequence[str],
        state_patch: Mapping[str, Any] | None = None,
    ) -> WorkSnapshot:
        if not evidence_refs:
            raise ProtocolViolation(
                "completed work requires at least one evidence reference"
            )
        current = self.get(work_id)
        if current.version != expected_version:
            raise ProtocolViolation(
                "stale work version: "
                f"expected {expected_version}, current {current.version}"
            )
        if current.owner != agent:
            raise ProtocolViolation(
                "only the current work owner may complete work"
            )
        if current.status is WorkStatus.DONE:
            raise ProtocolViolation("completed work is immutable")
        return self._commit(
            work_id=work_id,
            expected_version=expected_version,
            actor=agent,
            operation="complete",
            updated_at=completed_at,
            new_status=WorkStatus.DONE,
            state_patch=state_patch,
            evidence_refs=evidence_refs,
        )

    def project(
        self,
        *,
        work_id: str,
        consumer: Principal,
        recent_event_limit: int = 8,
    ) -> ContextProjection:
        if recent_event_limit <= 0:
            raise ProtocolViolation(
                "recent_event_limit must be positive"
            )
        with self._connect() as connection:
            connection.execute("BEGIN")
            work = self._read_snapshot(connection, work_id)
            rows = connection.execute(
                """
                SELECT *
                FROM work_events
                WHERE work_id = ?
                ORDER BY to_version DESC
                LIMIT ?
                """,
                (work_id, recent_event_limit),
            ).fetchall()
            connection.execute("COMMIT")
        events = tuple(
            reversed(
                tuple(self._event_from_row(row) for row in rows)
            )
        )
        return ContextProjection.seal(
            consumer=consumer,
            work=work,
            recent_events=events,
        )
