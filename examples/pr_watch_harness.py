"""Experimental persistent GitHub PR watcher; not the ACP authority engine.

A crash cannot lose an observed notification intent: persist it first, then
idempotently hand it to the existing AttentionOutbox on the next tick.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from attention_plane import AttentionEvent, AttentionOutbox, AttentionPreferences, EventKind

_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class WatchError(ValueError):
    pass


class WatchConflict(WatchError):
    """Optimistic-version conflict or disallowed state transition."""
    pass


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WatchError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PullSnapshot:
    head_sha: str
    state: str  # OPEN | MERGED | CLOSED
    ci: str  # SUCCESS | FAILURE | PENDING | UNKNOWN
    review: str = "NONE"  # NONE | CHANGES_REQUESTED
    failed_checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _SHA.fullmatch(self.head_sha):
            raise WatchError("invalid PR head SHA")
        if self.state not in ("OPEN", "MERGED", "CLOSED"):
            raise WatchError("invalid pull request state")
        if self.ci not in ("SUCCESS", "FAILURE", "PENDING", "UNKNOWN"):
            raise WatchError("invalid CI observation")
        if self.review not in ("NONE", "CHANGES_REQUESTED"):
            raise WatchError("invalid review observation")
        if any(not isinstance(name, str) or not name for name in self.failed_checks):
            raise WatchError("invalid CI check identity")

    @classmethod
    def from_dict(cls, value: dict) -> "PullSnapshot":
        if not isinstance(value, dict):
            raise WatchError("pull observation must be an object")
        return cls(
            head_sha=value["head_sha"], state=value["state"], ci=value["ci"],
            review=value.get("review", "NONE"),
            failed_checks=tuple(sorted(set(value.get("failed_checks", ())))),
        )

    def signature(self) -> str:
        return fingerprint({
            "head_sha": self.head_sha.lower(), "state": self.state,
            "ci": self.ci, "review": self.review,
            "failed_checks": self.failed_checks,
        })


@dataclass(frozen=True)
class Goal:
    user_id: str
    goal_id: str
    repo: str
    number: int
    state: str
    version: int
    phase: str
    signature: str


class PullReader(Protocol):
    def fetch(self, repo: str, number: int) -> PullSnapshot: ...


def _phase(s: PullSnapshot) -> str:
    if s.state == "MERGED":
        return "MERGED"
    if s.state == "CLOSED":
        return "CLOSED"
    if s.ci == "FAILURE":
        return "CI_BLOCKED"
    if s.review == "CHANGES_REQUESTED":
        return "REVIEW_BLOCKED"
    if s.ci == "SUCCESS":
        return "CI_CLEARED"
    return "WAITING"


def _notice(goal: Goal, s: PullSnapshot, prior_phase: str) -> tuple[EventKind, str]:
    ref = f"{goal.repo}#{goal.number}"
    phase = _phase(s)
    if phase == "MERGED":
        return EventKind.TASK_SUCCEEDED, f"{ref} has been merged (observed)."
    if phase == "CLOSED":
        return EventKind.ACTION_REQUIRED, f"{ref} was closed without merge; review goal."
    if phase == "CI_BLOCKED":
        # Provider check names are untrusted external content. Never embed them.
        return EventKind.ACTION_REQUIRED, f"{ref} CI failed at {s.head_sha[:12]}; review required."
    if phase == "REVIEW_BLOCKED":
        return EventKind.ACTION_REQUIRED, f"{ref} has an outstanding change request."
    if phase == "CI_CLEARED" and prior_phase in ("CI_BLOCKED", "REVIEW_BLOCKED"):
        return EventKind.TASK_SUCCEEDED, f"{ref} prior blocker cleared; merge eligibility unverified."
    return EventKind.ROUTINE_OK, f"{ref} observed without an actionable change."


class GoalStore:
    """Single-node SQLite reference; caller must enforce authenticated user scope."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = str(db_path)
        AttentionOutbox(self.path)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS pr_watch_goals (
                    user_id TEXT NOT NULL, goal_id TEXT NOT NULL,
                    repo TEXT NOT NULL, number INTEGER NOT NULL,
                    state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL DEFAULT '', signature TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, goal_id)
                );
                CREATE TABLE IF NOT EXISTS pr_watch_events (
                    user_id TEXT NOT NULL, goal_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    kind TEXT NOT NULL, summary TEXT NOT NULL, dedupe_key TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, emitted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, goal_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS pr_watch_control_intents (
                    user_id TEXT NOT NULL, goal_id TEXT NOT NULL,
                    version INTEGER NOT NULL, action TEXT NOT NULL,
                    signal_state TEXT NOT NULL DEFAULT 'PENDING',
                    checked_at TEXT, evidence TEXT,
                    PRIMARY KEY (user_id, goal_id, version)
                );
                CREATE TABLE IF NOT EXISTS pr_watch_control_requests (
                    user_id TEXT NOT NULL, goal_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, action TEXT NOT NULL,
                    expected_version INTEGER NOT NULL CHECK(expected_version>=0),
                    state TEXT NOT NULL DEFAULT 'PREPARED'
                        CHECK(state IN ('PREPARED','COMMITTED')),
                    committed_version INTEGER,
                    PRIMARY KEY (user_id,request_id),
                    FOREIGN KEY (user_id,goal_id)
                        REFERENCES pr_watch_goals(user_id,goal_id),
                    CHECK ((state='PREPARED' AND committed_version IS NULL)
                        OR (state='COMMITTED' AND committed_version=expected_version+1))
                );
                CREATE TABLE IF NOT EXISTS pr_watch_launches (
                    user_id TEXT NOT NULL, goal_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'PENDING',
                    evidence TEXT, workflow_status TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (user_id, goal_id),
                    FOREIGN KEY (user_id,goal_id)
                        REFERENCES pr_watch_goals(user_id,goal_id)
                );
            """)

    def _connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def register(self, *, user_id: str, goal_id: str, repo: str, number: int) -> Goal:
        if (
            not user_id or not goal_id or not isinstance(repo, str)
            or not _REPO.fullmatch(repo) or type(number) is not int or number < 1
        ):
            raise WatchError("invalid scoped goal or GitHub repository")
        if repo.split("/")[0] in (".", "..") or repo.split("/")[1] in (".", ".."):
            raise WatchError("invalid repository path")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT repo,number FROM pr_watch_goals WHERE user_id=? AND goal_id=?",
                (user_id, goal_id),
            ).fetchone()
            if existing and (existing["repo"], existing["number"]) != (repo, number):
                raise WatchError("goal identity cannot be rebound to a different PR")
            if not existing:
                db.execute(
                    "INSERT INTO pr_watch_goals (user_id,goal_id,repo,number,state) VALUES (?,?,?,?,?)",
                    (user_id, goal_id, repo, number, "RUNNING"),
                )
            # The first start intent commits together with Goal creation.
            # Existing RUNNING goals from older installations enroll once.
            current = db.execute(
                "SELECT state FROM pr_watch_goals WHERE user_id=? AND goal_id=?",
                (user_id, goal_id),
            ).fetchone()
            if current["state"] == "RUNNING":
                db.execute(
                    """INSERT OR IGNORE INTO pr_watch_launches (user_id,goal_id)
                       VALUES (?,?)""",
                    (user_id, goal_id),
                )
        return self.get(user_id=user_id, goal_id=goal_id)

    def launch(self, *, user_id: str, goal_id: str) -> dict:
        """User-scoped enrollment status; separate from Goal execution state."""
        self.get(user_id=user_id, goal_id=goal_id)
        with self._connection() as db:
            row = db.execute(
                """SELECT user_id,goal_id,state,evidence,workflow_status,updated_at
                   FROM pr_watch_launches WHERE user_id=? AND goal_id=?""",
                (user_id, goal_id),
            ).fetchone()
        if row is None:
            raise WatchError("launch not found within user scope")
        return dict(row)

    def pending_launches(self, *, limit: int = 100) -> list[dict]:
        """Trusted single-node service sweep ONLY; never route to a user API."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise WatchError("invalid launch sweep limit")
        with self._connection() as db:
            rows = db.execute(
                """SELECT user_id,goal_id,state FROM pr_watch_launches
                   WHERE state IN ('PENDING','UNKNOWN')
                   ORDER BY user_id,goal_id LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_launch(self, *, user_id: str, goal_id: str) -> dict:
        """Exactly one caller leaves PENDING; never resend an ambiguous start."""
        claimed_now = False
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            goal = db.execute(
                "SELECT state FROM pr_watch_goals WHERE user_id=? AND goal_id=?",
                (user_id, goal_id),
            ).fetchone()
            row = db.execute(
                "SELECT state FROM pr_watch_launches WHERE user_id=? AND goal_id=?",
                (user_id, goal_id),
            ).fetchone()
            if goal is None or row is None:
                raise WatchError("launch not found within user scope")
            if row["state"] == "PENDING":
                state = (
                    "IN_FLIGHT"
                    if goal["state"] in ("RUNNING", "PAUSED")
                    else "SKIPPED"
                )
                updated = db.execute(
                    """UPDATE pr_watch_launches SET state=?,evidence=?,
                          updated_at=datetime('now')
                       WHERE user_id=? AND goal_id=? AND state='PENDING'""",
                    (
                        state, None if state == "IN_FLIGHT" else "GOAL_INACTIVE",
                        user_id, goal_id,
                    ),
                )
                claimed_now = updated.rowcount == 1 and state == "IN_FLIGHT"
        return {
            **self.launch(user_id=user_id, goal_id=goal_id),
            "claimed_now": claimed_now,
        }

    def record_launch(
        self, *, user_id: str, goal_id: str, state: str,
        evidence: str, workflow_status: str | None = None,
    ) -> dict:
        """CAS progression. Never downgrade a server ACK or invent absence."""
        allowed = {
            "IN_FLIGHT": {"ACKED", "OBSERVED", "UNKNOWN"},
            "UNKNOWN": {"OBSERVED"},
            "ACKED": {"OBSERVED"},
        }
        proofs = {
            "ACKED": {"START_ACK"},
            "OBSERVED": {"DESCRIBE_EXISTS"},
            "UNKNOWN": {"START_UNCERTAIN", "CRASH_RECOVERY"},
        }
        if (
            state not in proofs or evidence not in proofs[state]
            or (
                workflow_status is not None
                and workflow_status not in (
                    "RUNNING", "COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"
                )
            )
        ):
            raise WatchError("invalid launch evidence")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM pr_watch_launches WHERE user_id=? AND goal_id=?",
                (user_id, goal_id),
            ).fetchone()
            if row is None:
                raise WatchError("launch not found within user scope")
            if row["state"] in allowed and state in allowed[row["state"]]:
                db.execute(
                    """UPDATE pr_watch_launches SET state=?,evidence=?,
                          workflow_status=?,updated_at=datetime('now')
                       WHERE user_id=? AND goal_id=?""",
                    (state, evidence, workflow_status, user_id, goal_id),
                )
        return self.launch(user_id=user_id, goal_id=goal_id)

    def recover_unacknowledged_launches(self) -> int:
        """Trusted exclusive startup ONLY: crash-in-flight becomes UNKNOWN."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                """UPDATE pr_watch_launches SET state='UNKNOWN',
                      evidence='CRASH_RECOVERY',updated_at=datetime('now')
                   WHERE state='IN_FLIGHT'"""
            )
            return result.rowcount

    def get(self, *, user_id: str, goal_id: str) -> Goal:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM pr_watch_goals WHERE user_id=? AND goal_id=?",
                (user_id, goal_id),
            ).fetchone()
        if row is None:
            raise WatchError("goal not found within user scope")
        return Goal(**dict(row))

    def list_goals(self, *, user_id: str, limit: int = 100) -> list[Goal]:
        if not user_id or not 1 <= limit <= 100:
            raise WatchError("invalid user scope or list limit")
        with self._connection() as db:
            rows = db.execute(
                """SELECT * FROM pr_watch_goals WHERE user_id=?
                   ORDER BY goal_id LIMIT ?""", (user_id, limit),
            ).fetchall()
        return [Goal(**dict(row)) for row in rows]

    def transition(
        self, *, user_id: str, goal_id: str, action: str, expected_version: int
    ) -> Goal:
        """CAS control action; no background provider mutations."""
        allowed = {
            "pause": ("RUNNING", "PAUSED"),
            "resume": ("PAUSED", "RUNNING"),
            "cancel": (("RUNNING", "PAUSED"), "CANCELLED"),
        }
        if action not in allowed or type(expected_version) is not int or expected_version < 0:
            raise WatchError("invalid action or expected version")
        valid_states, target = allowed[action]
        if isinstance(valid_states, str):
            valid_states = (valid_states,)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM pr_watch_goals WHERE user_id=? AND goal_id=?",
                (user_id, goal_id),
            ).fetchone()
            if row is None:
                raise WatchError("goal not found within user scope")
            if row["version"] != expected_version or row["state"] not in valid_states:
                raise WatchConflict("stale goal version or illegal lifecycle transition")
            db.execute(
                """UPDATE pr_watch_goals SET state=?,version=version+1
                   WHERE user_id=? AND goal_id=? AND version=?""",
                (target, user_id, goal_id, expected_version),
            )
            # Durable control intent is committed in the same SQLite transaction
            # as the goal CAS. Network signals happen AFTER commit.
            db.execute(
                """INSERT INTO pr_watch_control_intents
                   (user_id,goal_id,version,action)
                   VALUES (?,?,?,?)""",
                (user_id, goal_id, expected_version + 1, action),
            )
        return self.get(user_id=user_id, goal_id=goal_id)

    @staticmethod
    def _check_request_id(request_id: str) -> None:
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise WatchError("invalid client request ID")

    def prepare_control(
        self, *, user_id: str, goal_id: str, request_id: str,
        action: str, expected_version: int,
    ) -> dict:
        """Durably reserve a client request before any Goal CAS mutation."""
        self._check_request_id(request_id)
        if action not in ("pause", "resume", "cancel") or (
            type(expected_version) is not int or expected_version < 0
        ):
            raise WatchError("invalid control preparation")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                """SELECT * FROM pr_watch_control_requests
                   WHERE user_id=? AND request_id=?""",
                (user_id, request_id),
            ).fetchone()
            if old:
                if (old["goal_id"], old["action"], old["expected_version"]) != (
                    goal_id, action, expected_version
                ):
                    raise WatchConflict("request ID already bound to another command")
                return dict(old)
            goal = db.execute(
                "SELECT state,version FROM pr_watch_goals WHERE user_id=? AND goal_id=?",
                (user_id, goal_id),
            ).fetchone()
            if goal is None:
                raise WatchError("goal not found within user scope")
            valid = {
                "pause": ("RUNNING",),
                "resume": ("PAUSED",),
                "cancel": ("RUNNING", "PAUSED"),
            }
            if goal["version"] != expected_version or goal["state"] not in valid[action]:
                raise WatchConflict("stale goal version or illegal lifecycle transition")
            db.execute(
                """INSERT INTO pr_watch_control_requests
                   (user_id,goal_id,request_id,action,expected_version)
                   VALUES (?,?,?,?,?)""",
                (user_id, goal_id, request_id, action, expected_version),
            )
            return dict(db.execute(
                """SELECT * FROM pr_watch_control_requests
                   WHERE user_id=? AND request_id=?""",
                (user_id, request_id),
            ).fetchone())

    def control_request(self, *, user_id: str, request_id: str) -> dict:
        self._check_request_id(request_id)
        with self._connection() as db:
            row = db.execute(
                """SELECT * FROM pr_watch_control_requests
                   WHERE user_id=? AND request_id=?""",
                (user_id, request_id),
            ).fetchone()
        if row is None:
            raise WatchError("control request not found within user scope")
        return dict(row)

    def list_control_requests(self, *, user_id: str, limit: int = 100) -> list[dict]:
        if not user_id or type(limit) is not int or not 1 <= limit <= 100:
            raise WatchError("invalid scoped control request limit")
        with self._connection() as db:
            rows = db.execute(
                """SELECT * FROM pr_watch_control_requests
                   WHERE user_id=? ORDER BY goal_id,expected_version DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def transition_with_request(
        self, *, user_id: str, goal_id: str, action: str,
        expected_version: int, request_id: str,
    ) -> tuple[Goal, bool]:
        """One atomic CAS + control intent + request receipt. Replay has no signal."""
        self._check_request_id(request_id)
        valid = {
            "pause": (("RUNNING",), "PAUSED"),
            "resume": (("PAUSED",), "RUNNING"),
            "cancel": (("RUNNING", "PAUSED"), "CANCELLED"),
        }
        if action not in valid or type(expected_version) is not int or expected_version < 0:
            raise WatchError("invalid action or expected version")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            req = db.execute(
                """SELECT * FROM pr_watch_control_requests
                   WHERE user_id=? AND request_id=?""",
                (user_id, request_id),
            ).fetchone()
            if req is None:
                raise WatchConflict("prepare request before execution")
            if (req["goal_id"], req["action"], req["expected_version"]) != (
                goal_id, action, expected_version
            ):
                raise WatchConflict("request ID already bound to another command")
            if req["state"] == "COMMITTED":
                # Already committed: never replay the Temporal signal.
                replayed = True
            else:
                goal = db.execute(
                    """SELECT state,version FROM pr_watch_goals
                       WHERE user_id=? AND goal_id=?""",
                    (user_id, goal_id),
                ).fetchone()
                if goal is None:
                    raise WatchError("goal not found within user scope")
                sources, target = valid[action]
                if goal["state"] not in sources or goal["version"] != expected_version:
                    raise WatchConflict("stale goal version or illegal lifecycle transition")
                db.execute(
                    """UPDATE pr_watch_goals SET state=?,version=version+1
                       WHERE user_id=? AND goal_id=? AND version=?""",
                    (target, user_id, goal_id, expected_version),
                )
                db.execute(
                    """INSERT INTO pr_watch_control_intents
                       (user_id,goal_id,version,action) VALUES (?,?,?,?)""",
                    (user_id, goal_id, expected_version + 1, action),
                )
                db.execute(
                    """UPDATE pr_watch_control_requests
                       SET state='COMMITTED',committed_version=?
                       WHERE user_id=? AND request_id=?""",
                    (expected_version + 1, user_id, request_id),
                )
                replayed = False
        return self.get(user_id=user_id, goal_id=goal_id), replayed

    def control(self, *, user_id: str, goal_id: str, version: int) -> dict:
        if type(version) is not int or version < 0:
            raise WatchError("invalid control version")
        with self._connection() as db:
            row = db.execute(
                """SELECT user_id,goal_id,version,action,signal_state,
                          checked_at,evidence FROM pr_watch_control_intents
                   WHERE user_id=? AND goal_id=? AND version=?""",
                (user_id, goal_id, version),
            ).fetchone()
        if row is None:
            raise WatchError("control not found within user scope")
        return dict(row)

    def controls(self, *, user_id: str, goal_id: str) -> list[dict]:
        self.get(user_id=user_id, goal_id=goal_id)
        with self._connection() as db:
            rows = db.execute(
                """SELECT user_id,goal_id,version,action,signal_state,
                          checked_at,evidence FROM pr_watch_control_intents
                   WHERE user_id=? AND goal_id=?
                   ORDER BY version DESC LIMIT 50""",
                (user_id, goal_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_unsettled_controls(self, *, user_id: str, limit: int = 100) -> list[dict]:
        """Bounded, owner-scoped committed controls awaiting signal resolution.

        These rows prove the Goal CAS/intent committed, NOT that Temporal
        delivered or executed the requested control.
        """
        if not user_id or type(limit) is not int or not 1 <= limit <= 100:
            raise WatchError("invalid scoped receipt query")
        with self._connection() as db:
            rows = db.execute(
                """SELECT user_id,goal_id,version,action,signal_state,
                          checked_at,evidence FROM pr_watch_control_intents
                   WHERE user_id=? AND signal_state IN ('PENDING','UNKNOWN')
                   ORDER BY goal_id,version DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_control_signal(
        self, *, user_id: str, goal_id: str, version: int,
        state: str, evidence: str,
    ) -> dict:
        valid = ("ACKED", "UNKNOWN", "NO_WORKFLOW", "LOCAL_ONLY")
        if state not in valid or evidence not in (
            "SIGNAL_ACK", "HISTORY_PROOF", "ACK_UNCERTAIN",
            "NOT_FOUND", "BRIDGE_DISABLED",
        ):
            raise WatchError("invalid control signal evidence")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT signal_state FROM pr_watch_control_intents
                   WHERE user_id=? AND goal_id=? AND version=?""",
                (user_id, goal_id, version),
            ).fetchone()
            if row is None:
                raise WatchError("control not found within user scope")
            if row["signal_state"] == "ACKED" and state != "ACKED":
                # Later network failures cannot demote previously proven ACK.
                return self.control(user_id=user_id, goal_id=goal_id, version=version)
            if row["signal_state"] != "PENDING" and (
                row["signal_state"], state
            ) not in (("UNKNOWN", "ACKED"), ("NO_WORKFLOW", "ACKED")):
                return self.control(user_id=user_id, goal_id=goal_id, version=version)
            db.execute(
                """UPDATE pr_watch_control_intents SET signal_state=?,
                      checked_at=datetime('now'),evidence=?
                   WHERE user_id=? AND goal_id=? AND version=?""",
                (state, evidence, user_id, goal_id, version),
            )
        return self.control(user_id=user_id, goal_id=goal_id, version=version)

    def list_events(
        self, *, user_id: str, after: int = 0, limit: int = 100
    ) -> list[dict]:
        """Read-only durable per-user event cursor. emitted != delivered."""
        if not user_id or type(after) is not int or after < 0 or not 1 <= limit <= 100:
            raise WatchError("invalid event cursor or scope")
        with self._connection() as db:
            rows = db.execute(
                """SELECT rowid AS cursor,goal_id,event_id,kind,summary,
                          occurred_at,emitted
                   FROM pr_watch_events WHERE user_id=? AND rowid>?
                   ORDER BY rowid LIMIT ?""",
                (user_id, after, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def observe(self, goal: Goal, snapshot: PullSnapshot, *, now: datetime) -> str:
        """Atomically persist new provider state and a pending Attention intent."""
        now = utc(now)
        signature = snapshot.signature()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM pr_watch_goals WHERE user_id=? AND goal_id=?",
                (goal.user_id, goal.goal_id),
            ).fetchone()
            if row is None:
                raise WatchError("goal missing")
            if (row["repo"], row["number"]) != (goal.repo, goal.number):
                raise WatchError("goal reference drift")
            if row["state"] != "RUNNING":
                return "INACTIVE"
            if row["version"] != goal.version:
                # Concurrent read completed after another worker updated the goal.
                return "STALE"
            if row["signature"] == signature:
                return "UNCHANGED"
            current = Goal(**dict(row))
            phase = _phase(snapshot)
            kind, summary = _notice(current, snapshot, current.phase)
            event_id = f"pr-watch:{fingerprint((goal.user_id, goal.goal_id, phase, signature))}"
            db.execute(
                """INSERT OR IGNORE INTO pr_watch_events
                   (user_id,goal_id,event_id,kind,summary,dedupe_key,occurred_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (goal.user_id, goal.goal_id, event_id, kind.value,
                 summary, signature, now.isoformat()),
            )
            db.execute(
                """UPDATE pr_watch_goals SET phase=?,signature=?,version=version+1,
                   state=? WHERE user_id=? AND goal_id=?""",
                (phase, signature, "FINISHED" if phase in ("MERGED", "CLOSED") else "RUNNING",
                 goal.user_id, goal.goal_id),
            )
        return phase

    def drain(self, *, user_id: str, goal_id: str, now: datetime,
              preferences: AttentionPreferences | None = None) -> int:
        """Replay pending intent; this is NOT proof of user delivery."""
        now = utc(now)
        preferences = preferences or AttentionPreferences()
        with self._connection() as db:
            rows = db.execute(
                """SELECT * FROM pr_watch_events WHERE user_id=? AND goal_id=?
                   AND emitted=0 ORDER BY occurred_at,event_id""",
                (user_id, goal_id),
            ).fetchall()
        outbox = AttentionOutbox(self.path)
        for row in rows:
            outbox.submit(
                AttentionEvent(
                    event_id=row["event_id"], user_id=user_id,
                    goal_id=goal_id, dedupe_key=row["dedupe_key"],
                    kind=EventKind(row["kind"]), summary=row["summary"],
                    occurred_at=datetime.fromisoformat(row["occurred_at"]),
                ),
                preferences, now=now,
            )
            with self._connection() as db:
                db.execute(
                    """UPDATE pr_watch_events SET emitted=1
                       WHERE user_id=? AND goal_id=? AND event_id=? AND emitted=0""",
                    (user_id, goal_id, row["event_id"]),
                )
        return len(rows)

    def tick(self, *, user_id: str, goal_id: str, reader: PullReader,
             now: datetime, preferences: AttentionPreferences | None = None) -> dict:
        now = utc(now)
        self.drain(user_id=user_id, goal_id=goal_id, now=now, preferences=preferences)
        goal = self.get(user_id=user_id, goal_id=goal_id)
        if goal.state != "RUNNING":
            return {"state": goal.state, "phase": goal.phase, "new": False}
        # A failed GitHub read never changes durable state.
        snapshot = reader.fetch(goal.repo, goal.number)
        outcome = self.observe(goal, snapshot, now=now)
        self.drain(user_id=user_id, goal_id=goal_id, now=now, preferences=preferences)
        current = self.get(user_id=user_id, goal_id=goal_id)
        return {"state": current.state, "phase": current.phase,
                "new": outcome not in ("UNCHANGED", "STALE", "INACTIVE")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only PR watch Harness experiment")
    parser.add_argument("command", choices=["register", "tick", "status"])
    parser.add_argument("--db", required=True)
    parser.add_argument("--user", required=True, help="Trusted local scope; NOT authentication")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--fixture", help="Offline JSON PullSnapshot")
    args = parser.parse_args()
    store = GoalStore(args.db)
    now = datetime.now(timezone.utc)
    if args.command == "register":
        if args.repo is None or args.pr is None:
            parser.error("register requires --repo and --pr")
        result = store.register(
            user_id=args.user, goal_id=args.goal, repo=args.repo, number=args.pr
        ).__dict__
    elif args.command == "status":
        result = store.get(user_id=args.user, goal_id=args.goal).__dict__
    else:
        if args.fixture:
            snapshot = PullSnapshot.from_dict(json.loads(Path(args.fixture).read_text()))
            class FixtureReader:
                def fetch(self, repo: str, number: int) -> PullSnapshot:
                    return snapshot
            reader = FixtureReader()
        else:
            from pr_watch_github import GitHubReadOnlyReader
            reader = GitHubReadOnlyReader.from_environment()
        result = store.tick(user_id=args.user, goal_id=args.goal, reader=reader, now=now)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
