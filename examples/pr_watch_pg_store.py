"""Unified PostgreSQL reference for PR Watch state, controls, observations and Attention.

Uses the *same database* and transactions as PostgresAdmission's Goal + launch
journal. Not yet routed into the existing SQLite HTTP server or Temporal worker.
Install explicitly with install_v2(); no request-time DDL or silent mixed mode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from attention_plane import (
    AttentionEvent, AttentionPreferences, DeliveryState, Disposition,
    decide,
)
from pr_watch_harness import Goal, PullSnapshot, _notice, _phase, fingerprint, utc
from pr_watch_pg_admission import AdmissionError, PostgresAdmission, _id

SCHEMA_V2 = """
ALTER TABLE pr_watch_pg_goals ADD COLUMN IF NOT EXISTS phase TEXT NOT NULL DEFAULT '';
ALTER TABLE pr_watch_pg_goals ADD COLUMN IF NOT EXISTS signature TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS pr_watch_pg_controls (
  user_id TEXT NOT NULL, goal_id TEXT NOT NULL,
  version BIGINT NOT NULL CHECK(version > 0),
  action TEXT NOT NULL CHECK(action IN ('pause','resume','cancel')),
  signal_state TEXT NOT NULL DEFAULT 'PENDING'
    CHECK(signal_state IN ('PENDING','ACKED','UNKNOWN','NO_WORKFLOW','LOCAL_ONLY')),
  checked_at TIMESTAMPTZ, evidence TEXT,
  PRIMARY KEY(user_id,goal_id,version),
  FOREIGN KEY(user_id,goal_id) REFERENCES pr_watch_pg_goals(user_id,goal_id)
);

CREATE TABLE IF NOT EXISTS pr_watch_pg_events (
  cursor BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id TEXT NOT NULL, goal_id TEXT NOT NULL,
  event_id TEXT NOT NULL, kind TEXT NOT NULL, summary TEXT NOT NULL,
  dedupe_key TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL,
  emitted BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE(user_id,goal_id,event_id),
  FOREIGN KEY(user_id,goal_id) REFERENCES pr_watch_pg_goals(user_id,goal_id)
);
CREATE INDEX IF NOT EXISTS pr_watch_pg_events_cursor
  ON pr_watch_pg_events(user_id,cursor);

CREATE TABLE IF NOT EXISTS pr_watch_pg_attention_decisions (
  user_id TEXT NOT NULL, event_id TEXT NOT NULL,
  goal_id TEXT NOT NULL, disposition TEXT NOT NULL,
  reason TEXT NOT NULL, requires_approval BOOLEAN NOT NULL,
  notify_after TIMESTAMPTZ,
  PRIMARY KEY(user_id,event_id)
);
CREATE TABLE IF NOT EXISTS pr_watch_pg_deliveries (
  outbox_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL, goal_id TEXT NOT NULL, event_id TEXT NOT NULL,
  dedupe_key TEXT NOT NULL, summary TEXT NOT NULL,
  due_at TIMESTAMPTZ NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('PENDING','CLAIMED','DELIVERED','UNKNOWN')),
  owner TEXT, generation BIGINT NOT NULL DEFAULT 0,
  expires_at TIMESTAMPTZ, delivered_at TIMESTAMPTZ,
  reconciliation_evidence TEXT,
  UNIQUE(user_id,goal_id,dedupe_key),
  FOREIGN KEY(user_id,goal_id) REFERENCES pr_watch_pg_goals(user_id,goal_id)
);
CREATE INDEX IF NOT EXISTS pr_watch_pg_deliveries_due
  ON pr_watch_pg_deliveries(state,due_at);

CREATE TABLE IF NOT EXISTS pr_watch_pg_delivery_receipts (
  outbox_id TEXT PRIMARY KEY REFERENCES pr_watch_pg_deliveries(outbox_id),
  user_id TEXT NOT NULL, provider TEXT NOT NULL,
  recipient_digest TEXT NOT NULL,
  remote_message_id TEXT NOT NULL, accepted_at TIMESTAMPTZ NOT NULL,
  owner TEXT NOT NULL, generation BIGINT NOT NULL,
  UNIQUE(provider,recipient_digest,remote_message_id)
);
"""


@dataclass(frozen=True)
class PGDeliveryClaim:
    outbox_id: str
    user_id: str
    goal_id: str
    event_id: str
    summary: str
    owner: str
    generation: int
    expires_at: datetime


class PostgresWatchStore(PostgresAdmission):
    """Scoped reference store sharing one PostgreSQL transaction model."""

    def install_v2(self) -> None:
        """Explicit versioned V1->V2 migration; no implicit request-time DDL."""
        from pr_watch_pg_migrations import PostgresMigrations

        PostgresMigrations(self._dsn).upgrade(2)

    def validate_v2(self) -> None:
        """Fail closed on old, unversioned or drifted schemas, without DDL."""
        from pr_watch_pg_migrations import PostgresMigrations

        PostgresMigrations(self._dsn).require_minimum_version(2)
        with self._connection() as conn:
            for table in (
                "pr_watch_pg_goals", "pr_watch_pg_launches",
                "pr_watch_pg_controls", "pr_watch_pg_events",
                "pr_watch_pg_attention_decisions", "pr_watch_pg_deliveries",
                "pr_watch_pg_delivery_receipts",
            ):
                # Literal allowlisted identifiers; never use user input for SQL.
                conn.execute("SELECT 1 FROM " + table + " LIMIT 0")
            conn.execute("SELECT phase,signature FROM pr_watch_pg_goals LIMIT 0")

    def get(self, *, user_id: str, goal_id: str) -> dict:
        return self.goal(user_id=user_id, goal_id=goal_id)

    def list_goals(self, *, user_id: str, limit: int = 100) -> list[dict]:
        if not _id(user_id) or type(limit) is not int or not 1 <= limit <= 100:
            raise AdmissionError("invalid scoped goal listing")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT * FROM pr_watch_pg_goals WHERE user_id=%s
                   ORDER BY goal_id LIMIT %s""", (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def transition(
        self, *, user_id: str, goal_id: str, action: str, expected_version: int
    ) -> dict:
        allowed = {
            "pause": (("RUNNING",), "PAUSED"),
            "resume": (("PAUSED",), "RUNNING"),
            "cancel": (("RUNNING", "PAUSED"), "CANCELLED"),
        }
        if action not in allowed or type(expected_version) is not int or expected_version < 0:
            raise AdmissionError("invalid action or expected version")
        sources, target = allowed[action]
        with self._connection() as conn:
            row = conn.execute(
                """SELECT * FROM pr_watch_pg_goals WHERE user_id=%s AND goal_id=%s
                   FOR UPDATE""", (user_id, goal_id),
            ).fetchone()
            if row is None:
                raise AdmissionError("goal not found within user scope")
            if row["state"] not in sources or row["version"] != expected_version:
                raise AdmissionError("stale version or invalid lifecycle transition")
            version = expected_version + 1
            conn.execute(
                """UPDATE pr_watch_pg_goals SET state=%s,version=%s
                   WHERE user_id=%s AND goal_id=%s""",
                (target, version, user_id, goal_id),
            )
            conn.execute(
                """INSERT INTO pr_watch_pg_controls
                   (user_id,goal_id,version,action) VALUES (%s,%s,%s,%s)""",
                (user_id, goal_id, version, action),
            )
        return self.goal(user_id=user_id, goal_id=goal_id)

    @staticmethod
    def _check_request_id(request_id: str) -> None:
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise AdmissionError("invalid client request ID")

    def prepare_control(
        self, *, user_id: str, goal_id: str, request_id: str,
        action: str, expected_version: int,
    ) -> dict:
        """Persist a request before performing its Goal state transition."""
        self._check_request_id(request_id)
        if action not in ("pause", "resume", "cancel") or (
            type(expected_version) is not int or expected_version < 0
        ):
            raise AdmissionError("invalid control preparation")
        from pr_watch_pg_migrations import PostgresMigrations
        PostgresMigrations(self._dsn).require_version(3)
        with self._connection() as conn:
            # Serialize identical request keys even when competing Goal IDs differ.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                         (user_id + ":" + request_id,))
            goal = conn.execute(
                """SELECT state,version FROM pr_watch_pg_goals
                   WHERE user_id=%s AND goal_id=%s FOR UPDATE""",
                (user_id, goal_id),
            ).fetchone()
            if goal is None:
                raise AdmissionError("goal not found within user scope")
            existing = conn.execute(
                """SELECT * FROM pr_watch_pg_control_requests
                   WHERE user_id=%s AND request_id=%s FOR UPDATE""",
                (user_id, request_id),
            ).fetchone()
            if existing:
                if (existing["goal_id"], existing["action"], existing["expected_version"]) != (
                    goal_id, action, expected_version
                ):
                    raise AdmissionError("request ID already bound to another command")
                return dict(existing)
            valid = {
                "pause": ("RUNNING",),
                "resume": ("PAUSED",),
                "cancel": ("RUNNING", "PAUSED"),
            }
            if goal["version"] != expected_version or goal["state"] not in valid[action]:
                raise AdmissionError("stale goal version or illegal lifecycle transition")
            conn.execute(
                """INSERT INTO pr_watch_pg_control_requests
                   (user_id,goal_id,request_id,action,expected_version)
                   VALUES (%s,%s,%s,%s,%s)""",
                (user_id, goal_id, request_id, action, expected_version),
            )
            record = conn.execute(
                """SELECT * FROM pr_watch_pg_control_requests
                   WHERE user_id=%s AND request_id=%s""",
                (user_id, request_id),
            ).fetchone()
            return dict(record)

    def control_request(self, *, user_id: str, request_id: str) -> dict:
        self._check_request_id(request_id)
        with self._connection() as conn:
            row = conn.execute(
                """SELECT * FROM pr_watch_pg_control_requests
                   WHERE user_id=%s AND request_id=%s""",
                (user_id, request_id),
            ).fetchone()
        if row is None:
            raise AdmissionError("control request not found within user scope")
        return dict(row)

    def list_control_requests(self, *, user_id: str, limit: int = 100) -> list[dict]:
        if not _id(user_id) or type(limit) is not int or not 1 <= limit <= 100:
            raise AdmissionError("invalid scoped control request limit")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT * FROM pr_watch_pg_control_requests
                   WHERE user_id=%s ORDER BY goal_id,expected_version DESC LIMIT %s""",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def transition_with_request(
        self, *, user_id: str, goal_id: str, action: str,
        expected_version: int, request_id: str,
    ) -> tuple[dict, bool]:
        """CAS + journal + request receipt in one PG transaction; no signal on replay."""
        self._check_request_id(request_id)
        valid = {
            "pause": (("RUNNING",), "PAUSED"),
            "resume": (("PAUSED",), "RUNNING"),
            "cancel": (("RUNNING", "PAUSED"), "CANCELLED"),
        }
        if action not in valid or type(expected_version) is not int or expected_version < 0:
            raise AdmissionError("invalid action or expected version")
        from pr_watch_pg_migrations import PostgresMigrations
        PostgresMigrations(self._dsn).require_version(3)
        with self._connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                         (user_id + ":" + request_id,))
            goal = conn.execute(
                """SELECT state,version FROM pr_watch_pg_goals
                   WHERE user_id=%s AND goal_id=%s FOR UPDATE""",
                (user_id, goal_id),
            ).fetchone()
            if goal is None:
                raise AdmissionError("goal not found within user scope")
            req = conn.execute(
                """SELECT * FROM pr_watch_pg_control_requests
                   WHERE user_id=%s AND request_id=%s FOR UPDATE""",
                (user_id, request_id),
            ).fetchone()
            if req is None:
                raise AdmissionError("prepare request before execution")
            if (req["goal_id"], req["action"], req["expected_version"]) != (
                goal_id, action, expected_version
            ):
                raise AdmissionError("request ID already bound to another command")
            if req["state"] == "COMMITTED":
                replayed = True
            else:
                sources, target = valid[action]
                if goal["state"] not in sources or goal["version"] != expected_version:
                    raise AdmissionError("stale version or invalid lifecycle transition")
                conn.execute(
                    """UPDATE pr_watch_pg_goals SET state=%s,version=%s
                       WHERE user_id=%s AND goal_id=%s""",
                    (target, expected_version + 1, user_id, goal_id),
                )
                conn.execute(
                    """INSERT INTO pr_watch_pg_controls
                       (user_id,goal_id,version,action) VALUES (%s,%s,%s,%s)""",
                    (user_id, goal_id, expected_version + 1, action),
                )
                conn.execute(
                    """UPDATE pr_watch_pg_control_requests
                       SET state='COMMITTED',committed_version=%s
                       WHERE user_id=%s AND request_id=%s""",
                    (expected_version + 1, user_id, request_id),
                )
                replayed = False
        return self.goal(user_id=user_id, goal_id=goal_id), replayed

    def control(self, *, user_id: str, goal_id: str, version: int) -> dict:
        if not _id(user_id) or not _id(goal_id) or type(version) is not int or version < 1:
            raise AdmissionError("invalid control identity")
        with self._connection() as conn:
            row = conn.execute(
                """SELECT * FROM pr_watch_pg_controls
                   WHERE user_id=%s AND goal_id=%s AND version=%s""",
                (user_id, goal_id, version),
            ).fetchone()
        if row is None:
            raise AdmissionError("control not found within user scope")
        return dict(row)

    def controls(self, *, user_id: str, goal_id: str) -> list[dict]:
        self.goal(user_id=user_id, goal_id=goal_id)
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT * FROM pr_watch_pg_controls
                   WHERE user_id=%s AND goal_id=%s
                   ORDER BY version DESC LIMIT 50""", (user_id, goal_id),
            ).fetchall()
        return [dict(x) for x in rows]

    def list_unsettled_controls(self, *, user_id: str, limit: int = 100) -> list[dict]:
        """Read-only committed controls with unresolved Temporal signals."""
        if not _id(user_id) or type(limit) is not int or not 1 <= limit <= 100:
            raise AdmissionError("invalid scoped receipt query")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT user_id,goal_id,version,action,signal_state,
                          checked_at,evidence FROM pr_watch_pg_controls
                   WHERE user_id=%s AND signal_state IN ('PENDING','UNKNOWN')
                   ORDER BY goal_id,version DESC LIMIT %s""",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_control_signal(
        self, *, user_id: str, goal_id: str, version: int,
        state: str, evidence: str,
    ) -> dict:
        valid = {
            "ACKED": {"SIGNAL_ACK", "HISTORY_PROOF"},
            "UNKNOWN": {"ACK_UNCERTAIN"},
            "NO_WORKFLOW": {"NOT_FOUND"},
            "LOCAL_ONLY": {"BRIDGE_DISABLED"},
        }
        if state not in valid or evidence not in valid[state]:
            raise AdmissionError("invalid control signal evidence")
        with self._connection() as conn:
            row = conn.execute(
                """SELECT signal_state FROM pr_watch_pg_controls
                   WHERE user_id=%s AND goal_id=%s AND version=%s
                   FOR UPDATE""", (user_id, goal_id, version),
            ).fetchone()
            if row is None:
                raise AdmissionError("control not found within user scope")
            old = row["signal_state"]
            if old == "PENDING" or (state == "ACKED" and old in ("UNKNOWN", "NO_WORKFLOW")):
                conn.execute(
                    """UPDATE pr_watch_pg_controls SET
                       signal_state=%s,evidence=%s,checked_at=clock_timestamp()
                       WHERE user_id=%s AND goal_id=%s AND version=%s""",
                    (state, evidence, user_id, goal_id, version),
                )
        return self.control(user_id=user_id, goal_id=goal_id, version=version)

    def observe(
        self, goal: dict, snapshot: PullSnapshot, *,
        now: datetime, preferences: AttentionPreferences | None = None,
    ) -> str:
        """One transaction: CAS observation, event, policy decision, delivery."""
        at = utc(now)
        signature = snapshot.signature()
        with self._connection() as conn:
            row = conn.execute(
                """SELECT * FROM pr_watch_pg_goals
                   WHERE user_id=%s AND goal_id=%s FOR UPDATE""",
                (goal["user_id"], goal["goal_id"]),
            ).fetchone()
            if row is None:
                raise AdmissionError("goal not found within user scope")
            if (row["repo"], row["number"]) != (goal["repo"], goal["number"]):
                raise AdmissionError("goal selector drift")
            if row["state"] != "RUNNING":
                return "INACTIVE"
            if row["version"] != goal["version"]:
                return "STALE"
            if row["signature"] == signature:
                return "UNCHANGED"
            phase = _phase(snapshot)
            current = Goal(**{
                key: row[key] for key in (
                    "user_id", "goal_id", "repo", "number",
                    "state", "version", "phase", "signature",
                )
            })
            kind, summary = _notice(current, snapshot, current.phase)
            incident = f"pr-watch:{fingerprint((row['user_id'],row['goal_id'],phase,signature))}"
            event = AttentionEvent(
                event_id=incident, user_id=row["user_id"],
                goal_id=row["goal_id"], kind=kind, summary=summary,
                dedupe_key=signature, occurred_at=at,
            )
            decision = decide(event, preferences or AttentionPreferences(), now=at)
            conn.execute(
                """INSERT INTO pr_watch_pg_events
                   (user_id,goal_id,event_id,kind,summary,dedupe_key,occurred_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id,goal_id,event_id) DO NOTHING""",
                (
                    row["user_id"],row["goal_id"],incident,kind.value,summary,
                    signature,at,
                ),
            )
            conn.execute(
                """INSERT INTO pr_watch_pg_attention_decisions
                   (user_id,event_id,goal_id,disposition,reason,
                    requires_approval,notify_after)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id,event_id) DO NOTHING""",
                (
                    row["user_id"],incident,row["goal_id"],
                    decision.disposition.value,decision.reason,
                    decision.requires_approval,decision.notify_after,
                ),
            )
            if decision.disposition is Disposition.NOTIFY:
                conn.execute(
                    """INSERT INTO pr_watch_pg_deliveries
                       (outbox_id,user_id,goal_id,event_id,dedupe_key,
                        summary,due_at,state)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,'PENDING')
                       ON CONFLICT (user_id,goal_id,dedupe_key) DO NOTHING""",
                    (
                        uuid4().hex,row["user_id"],row["goal_id"],incident,
                        f"{kind.value}:{signature}",summary,
                        decision.notify_after,
                    ),
                )
            conn.execute(
                """UPDATE pr_watch_pg_goals
                   SET phase=%s,signature=%s,version=version+1,state=%s
                   WHERE user_id=%s AND goal_id=%s""",
                (
                    phase,signature,
                    "FINISHED" if phase in ("MERGED","CLOSED") else "RUNNING",
                    row["user_id"],row["goal_id"],
                ),
            )
        return phase

    def tick(
        self, *, user_id: str, goal_id: str, reader, now: datetime,
        preferences: AttentionPreferences | None = None,
    ) -> dict:
        goal = self.goal(user_id=user_id, goal_id=goal_id)
        if goal["state"] != "RUNNING":
            return {"state":goal["state"], "phase":goal["phase"], "new":False}
        # Read-only provider access occurs outside transaction; CAS fences
        # anything that returns after pause, cancel or another observation.
        snapshot = reader.fetch(goal["repo"], goal["number"])
        outcome = self.observe(goal, snapshot, now=now, preferences=preferences)
        current = self.goal(user_id=user_id, goal_id=goal_id)
        return {
            "state":current["state"],"phase":current["phase"],
            "new":outcome not in ("UNCHANGED","STALE","INACTIVE"),
        }

    def list_events(
        self, *, user_id: str, after: int = 0, limit: int = 100,
    ) -> list[dict]:
        if not _id(user_id) or type(after) is not int or after < 0 or not (
            type(limit) is int and 1 <= limit <= 100
        ):
            raise AdmissionError("invalid scoped event cursor")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT cursor,goal_id,event_id,kind,summary,occurred_at,emitted
                   FROM pr_watch_pg_events
                   WHERE user_id=%s AND cursor>%s ORDER BY cursor LIMIT %s""",
                (user_id,after,limit),
            ).fetchall()
        return [dict(x) for x in rows]

    def claim_due(
        self, *, user_id: str, owner: str, limit: int = 10,
        lease_seconds: int = 60,
    ) -> list[PGDeliveryClaim]:
        if not _id(user_id) or not _id(owner) or not (
            type(limit) is int and 1 <= limit <= 100
        ) or not (
            type(lease_seconds) is int and 1 <= lease_seconds <= 300
        ):
            raise AdmissionError("invalid notification claim")
        with self._connection() as conn:
            rows = conn.execute(
                """WITH due AS (
                    SELECT outbox_id FROM pr_watch_pg_deliveries
                    WHERE user_id=%s AND state='PENDING' AND due_at<=clock_timestamp()
                    ORDER BY due_at,outbox_id
                    FOR UPDATE SKIP LOCKED LIMIT %s
                   )
                   UPDATE pr_watch_pg_deliveries d
                   SET state='CLAIMED',owner=%s,generation=d.generation+1,
                       expires_at=clock_timestamp()+(%s * interval '1 second')
                   FROM due WHERE d.outbox_id=due.outbox_id
                   RETURNING d.outbox_id,d.user_id,d.goal_id,d.event_id,d.summary,
                             d.owner,d.generation,d.expires_at""",
                (user_id,limit,owner,lease_seconds),
            ).fetchall()
        return [PGDeliveryClaim(**dict(r)) for r in rows]

    def complete_delivery(
        self, claim: PGDeliveryClaim, *, state: DeliveryState,
        provider: str | None = None, remote_message_id: str | None = None,
        recipient_digest: str | None = None,
    ) -> None:
        """Provider ACK and receipt either both persist or neither does."""
        if state not in (DeliveryState.DELIVERED,DeliveryState.UNKNOWN):
            raise AdmissionError("invalid delivery final state")
        if state is DeliveryState.DELIVERED and not (
            provider == "telegram"
            and isinstance(remote_message_id,str)
            and remote_message_id.isdecimal()
            and 1 <= len(remote_message_id) <= 32
            and isinstance(recipient_digest,str)
            and len(recipient_digest) == 64
            and all(c in "0123456789abcdef" for c in recipient_digest)
        ):
            raise AdmissionError("provider acceptance requires a valid receipt")
        with self._connection() as conn:
            row = conn.execute(
                """UPDATE pr_watch_pg_deliveries
                   SET state=%s,delivered_at=CASE WHEN %s='DELIVERED'
                       THEN clock_timestamp() ELSE NULL END
                   WHERE outbox_id=%s AND user_id=%s AND state='CLAIMED'
                     AND owner=%s AND generation=%s
                     AND expires_at>clock_timestamp()
                   RETURNING outbox_id,delivered_at""",
                (
                    state.value,state.value,claim.outbox_id,claim.user_id,
                    claim.owner,claim.generation,
                ),
            ).fetchone()
            if row is None:
                raise AdmissionError("notification lease lost or expired")
            if state is DeliveryState.DELIVERED:
                conn.execute(
                    """INSERT INTO pr_watch_pg_delivery_receipts
                       (outbox_id,user_id,provider,recipient_digest,
                        remote_message_id,accepted_at,owner,generation)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        claim.outbox_id,claim.user_id,provider,recipient_digest,
                        remote_message_id,row["delivered_at"],claim.owner,
                        claim.generation,
                    ),
                )

    def recover_expired_deliveries(self, *, user_id: str) -> int:
        if not _id(user_id):
            raise AdmissionError("invalid recovery user scope")
        with self._connection() as conn:
            rows = conn.execute(
                """UPDATE pr_watch_pg_deliveries SET state='UNKNOWN'
                   WHERE user_id=%s AND state='CLAIMED'
                     AND expires_at<=clock_timestamp()
                   RETURNING outbox_id""", (user_id,),
            ).fetchall()
        return len(rows)

    def receipt(self, *, user_id: str, outbox_id: str) -> dict | None:
        if not _id(user_id) or not isinstance(outbox_id,str):
            raise AdmissionError("invalid receipt scope")
        with self._connection() as conn:
            row = conn.execute(
                """SELECT provider,recipient_digest,remote_message_id,
                          accepted_at,generation
                   FROM pr_watch_pg_delivery_receipts
                   WHERE user_id=%s AND outbox_id=%s""",
                (user_id,outbox_id),
            ).fetchone()
        return dict(row) if row else None

    def delivery_counts(self, *, user_id: str) -> dict[str,int]:
        if not _id(user_id):
            raise AdmissionError("invalid delivery scope")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT state,count(*) AS n FROM pr_watch_pg_deliveries
                   WHERE user_id=%s GROUP BY state""", (user_id,),
            ).fetchall()
        return {r["state"]:r["n"] for r in rows}
