"""Reference Attention Plane boundary (not an ACP production service).

Policy decisions and durable delivery intents are distinct from provider actions.
Unknown delivery outcomes are never retried automatically.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

from agent_control_plane.state_transition_protocol import (
    ProtocolViolation,
    canonical_digest,
)


class EventKind(str, Enum):
    ROUTINE_OK = "ROUTINE_OK"
    TASK_SUCCEEDED = "TASK_SUCCEEDED"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    CRITICAL = "CRITICAL"


class Disposition(str, Enum):
    SILENT = "SILENT"
    DIGEST = "DIGEST"
    NOTIFY = "NOTIFY"


class DeliveryState(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    DELIVERED = "DELIVERED"
    UNKNOWN = "UNKNOWN"


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation("timestamps must include a timezone")


@dataclass(frozen=True)
class AttentionEvent:
    event_id: str
    user_id: str
    goal_id: str
    dedupe_key: str
    kind: EventKind
    summary: str
    occurred_at: datetime
    verified_critical: bool = False

    def __post_init__(self) -> None:
        if not all(
            (self.event_id, self.user_id, self.goal_id, self.dedupe_key, self.summary)
        ):
            raise ProtocolViolation("attention event identity and summary are required")
        if not isinstance(self.kind, EventKind):
            raise ProtocolViolation("attention event kind must be an EventKind")
        _aware(self.occurred_at)
        if self.verified_critical != (self.kind is EventKind.CRITICAL):
            raise ProtocolViolation(
                "critical classification requires trusted provenance verification"
            )


@dataclass(frozen=True)
class QuietHours:
    start: time
    end: time
    timezone_name: str

    def __post_init__(self) -> None:
        if self.start.tzinfo or self.end.tzinfo:
            raise ProtocolViolation("quiet hours use local wall clock times")
        ZoneInfo(self.timezone_name)


@dataclass(frozen=True)
class AttentionPreferences:
    mode: str = "balanced"  # quiet | balanced | all
    quiet_hours: QuietHours | None = None
    allow_critical_during_quiet: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("quiet", "balanced", "all"):
            raise ProtocolViolation("unknown attention mode")


@dataclass(frozen=True)
class Decision:
    disposition: Disposition
    reason: str
    requires_approval: bool
    notify_after: datetime | None


def _quiet_end(now: datetime, hours: QuietHours | None) -> datetime | None:
    if hours is None or hours.start == hours.end:
        return None
    local = now.astimezone(ZoneInfo(hours.timezone_name))
    clock = local.timetz().replace(tzinfo=None)
    if hours.start < hours.end:
        active = hours.start <= clock < hours.end
        end_date = local.date()
    else:
        active = clock >= hours.start or clock < hours.end
        end_date = (
            local.date() + timedelta(days=1)
            if clock >= hours.start
            else local.date()
        )
    if not active:
        return None
    return datetime.combine(
        end_date, hours.end, tzinfo=ZoneInfo(hours.timezone_name)
    ).astimezone(timezone.utc)


def decide(
    event: AttentionEvent,
    preferences: AttentionPreferences,
    *,
    now: datetime,
) -> Decision:
    """Deterministic reference policy; caller must authenticate event provenance."""
    _aware(now)
    if event.kind is EventKind.ROUTINE_OK:
        return Decision(Disposition.SILENT, "ROUTINE_NO_CHANGE", False, None)
    if event.kind is EventKind.TASK_SUCCEEDED:
        if preferences.mode == "quiet":
            return Decision(Disposition.SILENT, "USER_QUIET_MODE", False, None)
        if preferences.mode == "balanced":
            return Decision(Disposition.DIGEST, "BATCH_NONURGENT_RESULT", False, None)
        reason = "USER_ALL_MODE"
    elif event.kind is EventKind.ACTION_REQUIRED:
        reason = "BLOCKED_REQUIRES_USER"
    else:
        reason = "VERIFIED_CRITICAL"

    quiet_end = _quiet_end(now, preferences.quiet_hours)
    if event.kind is EventKind.CRITICAL and preferences.allow_critical_during_quiet:
        quiet_end = None
    return Decision(
        Disposition.NOTIFY,
        reason,
        event.kind is EventKind.ACTION_REQUIRED,
        quiet_end or now.astimezone(timezone.utc),
    )


@dataclass(frozen=True)
class DeliveryLease:
    outbox_id: str
    user_id: str
    goal_id: str
    event_id: str
    summary: str
    owner: str
    generation: int
    expires_at: datetime


class AttentionOutbox:
    """SQLite reference. Dispatch belongs to an external authenticated adapter.

    A CLAIMED delivery that loses its acknowledgement becomes UNKNOWN. It is
    never automatically reclaimed; provider reconciliation must precede retry.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    user_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL,
                    notify_after TEXT,
                    PRIMARY KEY (user_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    outbox_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    owner TEXT,
                    generation INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT,
                    delivered_at TEXT,
                    reconciliation_evidence TEXT,
                    UNIQUE (user_id, goal_id, dedupe_key)
                );
                CREATE INDEX IF NOT EXISTS deliveries_claim_idx
                  ON deliveries (state, due_at);
                """
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def submit(
        self,
        event: AttentionEvent,
        preferences: AttentionPreferences,
        *,
        now: datetime,
    ) -> Decision:
        """Atomically log a decision and deduplicate delivery intent by user/goal."""
        _aware(now)
        fingerprint = canonical_digest(event)
        decision = decide(event, preferences, now=now)
        with self._transaction() as db:
            previous = db.execute(
                "SELECT * FROM decisions WHERE user_id=? AND event_id=?",
                (event.user_id, event.event_id),
            ).fetchone()
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise ProtocolViolation("event identity reused with different payload")
                return Decision(
                    Disposition(previous["disposition"]),
                    previous["reason"],
                    bool(previous["requires_approval"]),
                    (
                        datetime.fromisoformat(previous["notify_after"])
                        if previous["notify_after"]
                        else None
                    ),
                )
            db.execute(
                """INSERT INTO decisions
                   (user_id, event_id, goal_id, fingerprint,
                    disposition, reason, requires_approval, notify_after)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.user_id,
                    event.event_id,
                    event.goal_id,
                    fingerprint,
                    decision.disposition.value,
                    decision.reason,
                    int(decision.requires_approval),
                    decision.notify_after.isoformat() if decision.notify_after else None,
                ),
            )
            if decision.disposition is Disposition.NOTIFY:
                # The caller must scope dedupe_key to one specific incident/revision,
                # not to an entire recurring topic or an unbounded time period.
                db.execute(
                    """INSERT OR IGNORE INTO deliveries
                       (outbox_id, user_id, goal_id, event_id, dedupe_key,
                        summary, due_at, state)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        uuid4().hex,
                        event.user_id,
                        event.goal_id,
                        event.event_id,
                        f"{event.kind.value}:{event.dedupe_key}",
                        event.summary,
                        decision.notify_after.isoformat(),
                        DeliveryState.PENDING.value,
                    ),
                )
        return decision

    def claim_due(
        self, *, user_id: str, owner: str, now: datetime, limit: int = 10,
        lease_seconds: int = 60,
    ) -> list[DeliveryLease]:
        """Claim PENDING only. Never reclaim CLAIMED or UNKNOWN on lease expiry."""
        _aware(now)
        if not user_id or not owner or not 1 <= limit <= 100 or lease_seconds <= 0:
            raise ProtocolViolation("invalid notification delivery claim")
        expires_at = now + timedelta(seconds=lease_seconds)
        result: list[DeliveryLease] = []
        with self._transaction() as db:
            rows = db.execute(
                """SELECT * FROM deliveries
                   WHERE user_id=? AND state=? AND due_at<=?
                   ORDER BY due_at, outbox_id LIMIT ?""",
                (user_id, DeliveryState.PENDING.value, now.astimezone(timezone.utc).isoformat(), limit),
            ).fetchall()
            for row in rows:
                generation = row["generation"] + 1
                db.execute(
                    """UPDATE deliveries SET state=?, owner=?, generation=?,
                       expires_at=? WHERE outbox_id=? AND state=?""",
                    (
                        DeliveryState.CLAIMED.value, owner, generation,
                        expires_at.isoformat(), row["outbox_id"],
                        DeliveryState.PENDING.value,
                    ),
                )
                result.append(
                    DeliveryLease(
                        row["outbox_id"], row["user_id"], row["goal_id"],
                        row["event_id"], row["summary"], owner, generation,
                        expires_at,
                    )
                )
        return result

    def _complete_claim(
        self, lease: DeliveryLease, *, now: datetime, state: DeliveryState
    ) -> None:
        _aware(now)
        with self._transaction() as db:
            updated = db.execute(
                """UPDATE deliveries SET state=?, delivered_at=?
                   WHERE outbox_id=? AND user_id=? AND state=? AND owner=?
                     AND generation=? AND expires_at>?""",
                (
                    state.value,
                    now.isoformat() if state is DeliveryState.DELIVERED else None,
                    lease.outbox_id, lease.user_id, DeliveryState.CLAIMED.value,
                    lease.owner, lease.generation, now.astimezone(timezone.utc).isoformat(),
                ),
            )
            if updated.rowcount != 1:
                raise ProtocolViolation("delivery lease lost or already finalized")

    def mark_delivered(self, lease: DeliveryLease, *, now: datetime) -> None:
        """Call only after a confirmed provider receipt, not after a timeout."""
        self._complete_claim(lease, now=now, state=DeliveryState.DELIVERED)

    def mark_unknown(self, lease: DeliveryLease, *, now: datetime) -> None:
        """Record uncertain send outcome; no automatic retry."""
        self._complete_claim(lease, now=now, state=DeliveryState.UNKNOWN)

    def recover_expired(self, *, now: datetime) -> int:
        _aware(now)
        with self._transaction() as db:
            result = db.execute(
                """UPDATE deliveries SET state=?
                   WHERE state=? AND expires_at<=?""",
                (
                    DeliveryState.UNKNOWN.value,
                    DeliveryState.CLAIMED.value, now.astimezone(timezone.utc).isoformat(),
                ),
            )
            return result.rowcount

    def requeue_verified_absent(
        self, *, user_id: str, outbox_id: str, evidence_ref: str
    ) -> None:
        """Caller independently verified the provider did NOT deliver."""
        if not user_id or not outbox_id or not evidence_ref.strip():
            raise ProtocolViolation("requeue requires user scope and absence evidence")
        with self._transaction() as db:
            updated = db.execute(
                """UPDATE deliveries SET state=?, owner=NULL, expires_at=NULL,
                   reconciliation_evidence=?
                   WHERE user_id=? AND outbox_id=? AND state=?""",
                (
                    DeliveryState.PENDING.value, evidence_ref, user_id, outbox_id,
                    DeliveryState.UNKNOWN.value,
                ),
            )
            if updated.rowcount != 1:
                raise ProtocolViolation("notification is not UNKNOWN in user scope")

    def count(self, *, user_id: str, state: DeliveryState) -> int:
        with self._transaction() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM deliveries WHERE user_id=? AND state=?",
                (user_id, state.value),
            ).fetchone()
            return int(row["n"])
