from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .state_transition_protocol import (
    Principal,
    ProtocolViolation,
    canonical_digest,
)


_GENESIS_HASH = "GENESIS"


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
            "context overlay payload must be canonical JSON"
        ) from exc
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ProtocolViolation("context overlay payload must be an object")
    return decoded


@dataclass(frozen=True)
class ContextEntry:
    entry_id: str
    work_id: str
    revision: int
    actor: Principal
    entry_type: str
    payload: Mapping[str, Any]
    prior_entry_hash: str
    created_at: datetime
    entry_hash: str
    entry_version: str = "context-entry/v1"

    @classmethod
    def seal(
        cls,
        *,
        entry_id: str,
        work_id: str,
        revision: int,
        actor: Principal,
        entry_type: str,
        payload: Mapping[str, Any],
        prior_entry_hash: str,
        created_at: datetime,
    ) -> "ContextEntry":
        if not entry_id or not work_id or not entry_type.strip():
            raise ProtocolViolation(
                "context entry id, work id and type are required"
            )
        if revision <= 0:
            raise ProtocolViolation(
                "context entry revision must be positive"
            )
        if not prior_entry_hash:
            raise ProtocolViolation(
                "context entry prior hash is required"
            )
        _require_aware(created_at, "created_at")
        payload_snapshot = _snapshot_mapping(payload)
        provisional = cls(
            entry_id=entry_id,
            work_id=work_id,
            revision=revision,
            actor=actor,
            entry_type=entry_type,
            payload=payload_snapshot,
            prior_entry_hash=prior_entry_hash,
            created_at=created_at,
            entry_hash="",
        )
        return cls(
            entry_id=provisional.entry_id,
            work_id=provisional.work_id,
            revision=provisional.revision,
            actor=provisional.actor,
            entry_type=provisional.entry_type,
            payload=provisional.payload,
            prior_entry_hash=provisional.prior_entry_hash,
            created_at=provisional.created_at,
            entry_hash=canonical_digest(
                provisional,
                exclude=("entry_hash",),
            ),
        )

    def verify(self) -> None:
        if not self.entry_id or not self.work_id or not self.entry_type.strip():
            raise ProtocolViolation(
                "context entry id, work id and type are required"
            )
        if self.revision <= 0:
            raise ProtocolViolation(
                "context entry revision must be positive"
            )
        if not isinstance(self.actor, Principal):
            raise ProtocolViolation(
                "context entry actor must be a Principal"
            )
        if not self.prior_entry_hash:
            raise ProtocolViolation(
                "context entry prior hash is required"
            )
        _require_aware(self.created_at, "created_at")
        _snapshot_mapping(self.payload)
        actual = canonical_digest(
            self,
            exclude=("entry_hash",),
        )
        if actual != self.entry_hash:
            raise ProtocolViolation(
                "context entry digest mismatch"
            )


def _verify_entry_tail(
    *,
    work_id: str,
    context_revision: int,
    head_hash: str,
    entries: Sequence[ContextEntry],
) -> None:
    if context_revision < 0:
        raise ProtocolViolation(
            "context revision cannot be negative"
        )
    if not head_hash:
        raise ProtocolViolation("context head hash is required")

    if context_revision == 0:
        if head_hash != _GENESIS_HASH or entries:
            raise ProtocolViolation(
                "empty context overlay must use GENESIS head"
            )
        return

    if not entries:
        raise ProtocolViolation(
            "non-empty context overlay requires recent entries"
        )

    previous: ContextEntry | None = None
    for entry in entries:
        entry.verify()
        if entry.work_id != work_id:
            raise ProtocolViolation(
                "context entry belongs to another work item"
            )
        if entry.revision > context_revision:
            raise ProtocolViolation(
                "context entry is newer than overlay revision"
            )
        if previous is not None:
            if entry.revision != previous.revision + 1:
                raise ProtocolViolation(
                    "context entry revisions are not contiguous"
                )
            if entry.prior_entry_hash != previous.entry_hash:
                raise ProtocolViolation(
                    "context entry hash chain is broken"
                )
        elif entry.revision == 1 and entry.prior_entry_hash != _GENESIS_HASH:
            raise ProtocolViolation(
                "first context entry has invalid prior hash"
            )
        previous = entry

    tail = entries[-1]
    if tail.revision != context_revision:
        raise ProtocolViolation(
            "context entry tail does not reach current revision"
        )
    if tail.entry_hash != head_hash:
        raise ProtocolViolation(
            "context entry tail hash does not match current head"
        )


@dataclass(frozen=True)
class ContextOverlay:
    work_id: str
    authority_version: int
    authority_snapshot_hash: str
    context_revision: int
    head_hash: str
    recent_entries: tuple[ContextEntry, ...]
    overlay_hash: str
    overlay_version: str = "context-overlay/v1"

    @classmethod
    def seal(
        cls,
        *,
        work_id: str,
        authority_version: int,
        authority_snapshot_hash: str,
        context_revision: int,
        head_hash: str,
        recent_entries: Sequence[ContextEntry],
    ) -> "ContextOverlay":
        if not work_id:
            raise ProtocolViolation("context overlay work_id is required")
        if authority_version <= 0:
            raise ProtocolViolation(
                "context overlay authority version must be positive"
            )
        if not authority_snapshot_hash:
            raise ProtocolViolation(
                "context overlay authority snapshot hash is required"
            )
        entries = tuple(recent_entries)
        _verify_entry_tail(
            work_id=work_id,
            context_revision=context_revision,
            head_hash=head_hash,
            entries=entries,
        )
        provisional = cls(
            work_id=work_id,
            authority_version=authority_version,
            authority_snapshot_hash=authority_snapshot_hash,
            context_revision=context_revision,
            head_hash=head_hash,
            recent_entries=entries,
            overlay_hash="",
        )
        return cls(
            work_id=provisional.work_id,
            authority_version=provisional.authority_version,
            authority_snapshot_hash=provisional.authority_snapshot_hash,
            context_revision=provisional.context_revision,
            head_hash=provisional.head_hash,
            recent_entries=provisional.recent_entries,
            overlay_hash=canonical_digest(
                provisional,
                exclude=("overlay_hash",),
            ),
        )

    def verify(self) -> None:
        if not self.work_id:
            raise ProtocolViolation("context overlay work_id is required")
        if self.authority_version <= 0:
            raise ProtocolViolation(
                "context overlay authority version must be positive"
            )
        if not self.authority_snapshot_hash:
            raise ProtocolViolation(
                "context overlay authority snapshot hash is required"
            )
        _verify_entry_tail(
            work_id=self.work_id,
            context_revision=self.context_revision,
            head_hash=self.head_hash,
            entries=self.recent_entries,
        )
        actual = canonical_digest(
            self,
            exclude=("overlay_hash",),
        )
        if actual != self.overlay_hash:
            raise ProtocolViolation(
                "context overlay digest mismatch"
            )


class SQLiteContextOverlayStore:
    """Append-only context that does not mutate authoritative WorkSnapshot.

    The overlay shares the same SQLite database as SQLiteWorkContextStore.
    WorkSnapshot.version remains the authoritative/CAS revision. Context
    revision advances independently and is fenced with its own expected value.
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
                CREATE TABLE IF NOT EXISTS work_context_heads (
                    work_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL CHECK (revision >= 0),
                    head_hash TEXT NOT NULL,
                    FOREIGN KEY(work_id) REFERENCES work_snapshots(work_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_context_entries (
                    entry_id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK (revision > 0),
                    actor_type TEXT NOT NULL,
                    actor_subject TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    prior_entry_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    entry_hash TEXT NOT NULL,
                    UNIQUE(work_id, revision),
                    FOREIGN KEY(work_id) REFERENCES work_snapshots(work_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_context_entries_work_revision
                ON work_context_entries(work_id, revision)
                """
            )

    @staticmethod
    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        _require_aware(parsed, "stored context timestamp")
        return parsed

    @classmethod
    def _entry_from_row(cls, row: sqlite3.Row) -> ContextEntry:
        entry = ContextEntry(
            entry_id=row["entry_id"],
            work_id=row["work_id"],
            revision=int(row["revision"]),
            actor=Principal(
                type=row["actor_type"],
                subject=row["actor_subject"],
            ),
            entry_type=row["entry_type"],
            payload=json.loads(row["payload_json"]),
            prior_entry_hash=row["prior_entry_hash"],
            created_at=cls._parse_time(row["created_at"]),
            entry_hash=row["entry_hash"],
        )
        entry.verify()
        return entry

    @staticmethod
    def _authority_row(
        connection: sqlite3.Connection,
        work_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT version, snapshot_hash
            FROM work_snapshots
            WHERE work_id = ?
            """,
            (work_id,),
        ).fetchone()
        if row is None:
            raise ProtocolViolation(
                f"work item does not exist: {work_id}"
            )
        return row

    @staticmethod
    def _ensure_head(
        connection: sqlite3.Connection,
        work_id: str,
    ) -> tuple[int, str]:
        row = connection.execute(
            """
            SELECT revision, head_hash
            FROM work_context_heads
            WHERE work_id = ?
            """,
            (work_id,),
        ).fetchone()
        if row is not None:
            return int(row["revision"]), str(row["head_hash"])

        connection.execute(
            """
            INSERT INTO work_context_heads (
                work_id,
                revision,
                head_hash
            )
            VALUES (?, 0, ?)
            """,
            (work_id, _GENESIS_HASH),
        )
        return 0, _GENESIS_HASH

    def append(
        self,
        *,
        work_id: str,
        expected_revision: int,
        actor: Principal,
        entry_type: str,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> ContextEntry:
        if expected_revision < 0:
            raise ProtocolViolation(
                "expected context revision cannot be negative"
            )
        _require_aware(created_at, "created_at")
        payload_snapshot = _snapshot_mapping(payload)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._authority_row(connection, work_id)
            current_revision, head_hash = self._ensure_head(
                connection,
                work_id,
            )
            if current_revision != expected_revision:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "stale context revision: "
                    f"expected {expected_revision}, "
                    f"current {current_revision}"
                )

            entry = ContextEntry.seal(
                entry_id=uuid4().hex,
                work_id=work_id,
                revision=current_revision + 1,
                actor=actor,
                entry_type=entry_type,
                payload=payload_snapshot,
                prior_entry_hash=head_hash,
                created_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO work_context_entries (
                    entry_id,
                    work_id,
                    revision,
                    actor_type,
                    actor_subject,
                    entry_type,
                    payload_json,
                    prior_entry_hash,
                    created_at,
                    entry_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id,
                    entry.work_id,
                    entry.revision,
                    entry.actor.type,
                    entry.actor.subject,
                    entry.entry_type,
                    json.dumps(
                        entry.payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                    entry.prior_entry_hash,
                    entry.created_at.isoformat(),
                    entry.entry_hash,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE work_context_heads
                SET revision = ?, head_hash = ?
                WHERE work_id = ? AND revision = ? AND head_hash = ?
                """,
                (
                    entry.revision,
                    entry.entry_hash,
                    work_id,
                    current_revision,
                    head_hash,
                ),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "context head compare-and-swap failed"
                )
            connection.execute("COMMIT")
        return entry

    def get(
        self,
        *,
        work_id: str,
        recent_entry_limit: int = 8,
    ) -> ContextOverlay:
        if recent_entry_limit <= 0:
            raise ProtocolViolation(
                "recent_entry_limit must be positive"
            )

        with self._connect() as connection:
            connection.execute("BEGIN")
            authority = self._authority_row(connection, work_id)
            head = connection.execute(
                """
                SELECT revision, head_hash
                FROM work_context_heads
                WHERE work_id = ?
                """,
                (work_id,),
            ).fetchone()
            if head is None:
                context_revision = 0
                head_hash = _GENESIS_HASH
                rows = []
            else:
                context_revision = int(head["revision"])
                head_hash = str(head["head_hash"])
                rows = connection.execute(
                    """
                    SELECT *
                    FROM work_context_entries
                    WHERE work_id = ?
                    ORDER BY revision DESC
                    LIMIT ?
                    """,
                    (work_id, recent_entry_limit),
                ).fetchall()
            connection.execute("COMMIT")

        entries = tuple(
            reversed(
                tuple(self._entry_from_row(row) for row in rows)
            )
        )
        return ContextOverlay.seal(
            work_id=work_id,
            authority_version=int(authority["version"]),
            authority_snapshot_hash=str(authority["snapshot_hash"]),
            context_revision=context_revision,
            head_hash=head_hash,
            recent_entries=entries,
        )

    def changes_since(
        self,
        *,
        work_id: str,
        revision: int,
    ) -> tuple[ContextEntry, ...]:
        if revision < 0:
            raise ProtocolViolation(
                "context revision cannot be negative"
            )

        with self._connect() as connection:
            connection.execute("BEGIN")
            self._authority_row(connection, work_id)
            head = connection.execute(
                """
                SELECT revision, head_hash
                FROM work_context_heads
                WHERE work_id = ?
                """,
                (work_id,),
            ).fetchone()
            current_revision = (
                int(head["revision"])
                if head is not None
                else 0
            )
            head_hash = (
                str(head["head_hash"])
                if head is not None
                else _GENESIS_HASH
            )
            if revision > current_revision:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "requested context revision is newer than current revision"
                )
            rows = connection.execute(
                """
                SELECT *
                FROM work_context_entries
                WHERE work_id = ? AND revision > ?
                ORDER BY revision ASC
                """,
                (work_id, revision),
            ).fetchall()
            connection.execute("COMMIT")

        if revision == current_revision:
            if rows:
                raise ProtocolViolation(
                    "context history contains entries past current revision"
                )
            return ()

        entries = tuple(self._entry_from_row(row) for row in rows)
        if not entries or entries[0].revision != revision + 1:
            raise ProtocolViolation(
                "context history does not continue from requested revision"
            )
        _verify_entry_tail(
            work_id=work_id,
            context_revision=current_revision,
            head_hash=head_hash,
            entries=entries,
        )
        return entries
