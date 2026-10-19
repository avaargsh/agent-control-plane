"""PostgreSQL-first *admission* slice for PR Watch (not the active SQLite API).

Atomic goal registration + one-shot Workflow start admission with PostgreSQL
row locks and owner/generation fencing. It intentionally does NOT expose a
mixed SQLite/Postgres service or claim multi-instance Harness correctness.
Install with: pip install -e '.[pg-pilot]'
"""
from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass

from psycopg import connect
from psycopg.rows import dict_row

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_STATES = {"RUNNING", "PAUSED", "CANCELLED", "FINISHED"}
_REMOTE_STATES = {
    "RUNNING", "COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT",
}

# Operator-installed v1 schema, never auto-migrated as a side effect of a read.
# Database clock, not callers' wall clocks, decides whether a claim expires.
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS pr_watch_pg_goals (
  user_id TEXT NOT NULL,
  goal_id TEXT NOT NULL,
  repo TEXT NOT NULL,
  number INTEGER NOT NULL CHECK (number > 0),
  state TEXT NOT NULL CHECK (state IN ('RUNNING','PAUSED','CANCELLED','FINISHED')),
  version BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, goal_id)
);
CREATE TABLE IF NOT EXISTS pr_watch_pg_launches (
  user_id TEXT NOT NULL,
  goal_id TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (state IN ('PENDING','IN_FLIGHT','UNKNOWN','ACKED','OBSERVED','SKIPPED')),
  owner TEXT,
  generation BIGINT NOT NULL DEFAULT 0,
  claim_deadline TIMESTAMPTZ,
  evidence TEXT,
  workflow_status TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (user_id, goal_id),
  FOREIGN KEY (user_id, goal_id)
    REFERENCES pr_watch_pg_goals(user_id, goal_id)
);
CREATE INDEX IF NOT EXISTS pr_watch_pg_launches_pending
  ON pr_watch_pg_launches (state, updated_at);
"""


class AdmissionError(ValueError):
    """A scoped request is invalid or attempts an unsafe lifecycle change."""


@dataclass(frozen=True)
class LaunchClaim:
    user_id: str
    goal_id: str
    state: str
    owner: str | None
    generation: int
    claimed_now: bool


def _id(value: str) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


class PostgresAdmission:
    """Narrow PostgreSQL enrollment contract; NOT a drop-in GoalStore."""

    def __init__(self, dsn: str) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise AdmissionError("PostgreSQL DSN must be configured privately")
        self._dsn = dsn

    def _connection(self):
        return connect(self._dsn, row_factory=dict_row, connect_timeout=5)

    def install_v1(self) -> None:
        """Explicit operator migration, audited by schema version and checksum."""
        from pr_watch_pg_migrations import PostgresMigrations

        PostgresMigrations(self._dsn).upgrade(1)

    def register(
        self, *, user_id: str, goal_id: str, repo: str, number: int
    ) -> dict:
        if (
            not _id(user_id) or not _id(goal_id) or not isinstance(repo, str)
            or not _REPO.fullmatch(repo) or type(number) is not int or number <= 0
            or any(part in (".", "..") for part in repo.split("/"))
        ):
            raise AdmissionError("invalid scoped registration")
        with self._connection() as conn:
            # ON CONFLICT serializes concurrent registration under the PK.
            conn.execute(
                """INSERT INTO pr_watch_pg_goals
                   (user_id, goal_id, repo, number, state)
                   VALUES (%s,%s,%s,%s,'RUNNING')
                   ON CONFLICT (user_id,goal_id) DO NOTHING""",
                (user_id, goal_id, repo, number),
            )
            row = conn.execute(
                """SELECT * FROM pr_watch_pg_goals
                   WHERE user_id=%s AND goal_id=%s FOR UPDATE""",
                (user_id, goal_id),
            ).fetchone()
            if row["repo"] != repo or row["number"] != number:
                raise AdmissionError("registered goal cannot be rebound")
            if row["state"] == "RUNNING":
                # Same transaction as the original Goal insert. Replays
                # cannot reset ACKED/UNKNOWN back to PENDING.
                conn.execute(
                    """INSERT INTO pr_watch_pg_launches (user_id,goal_id)
                       VALUES (%s,%s)
                       ON CONFLICT (user_id,goal_id) DO NOTHING""",
                    (user_id, goal_id),
                )
        return dict(row)

    def goal(self, *, user_id: str, goal_id: str) -> dict:
        if not _id(user_id) or not _id(goal_id):
            raise AdmissionError("invalid scoped goal lookup")
        with self._connection() as conn:
            row = conn.execute(
                """SELECT * FROM pr_watch_pg_goals
                   WHERE user_id=%s AND goal_id=%s""",
                (user_id, goal_id),
            ).fetchone()
        if row is None:
            raise AdmissionError("goal not found within user scope")
        return dict(row)

    def set_goal_state(
        self, *, user_id: str, goal_id: str, state: str, expected_version: int
    ) -> dict:
        """Test-only CAS lifecycle for admission; not ACP action authority."""
        if state not in _STATES or type(expected_version) is not int or expected_version < 0:
            raise AdmissionError("invalid CAS parameters")
        allowed = {
            "RUNNING": {"PAUSED", "CANCELLED", "FINISHED"},
            "PAUSED": {"RUNNING", "CANCELLED"},
        }
        with self._connection() as conn:
            row = conn.execute(
                """SELECT state,version FROM pr_watch_pg_goals
                   WHERE user_id=%s AND goal_id=%s FOR UPDATE""",
                (user_id, goal_id),
            ).fetchone()
            if row is None:
                raise AdmissionError("goal not found within user scope")
            if row["version"] != expected_version or state not in allowed.get(row["state"], set()):
                raise AdmissionError("stale version or invalid lifecycle transition")
            conn.execute(
                """UPDATE pr_watch_pg_goals SET state=%s,version=version+1
                   WHERE user_id=%s AND goal_id=%s""",
                (state, user_id, goal_id),
            )
        return self.goal(user_id=user_id, goal_id=goal_id)

    def launch(self, *, user_id: str, goal_id: str) -> dict:
        self.goal(user_id=user_id, goal_id=goal_id)
        with self._connection() as conn:
            row = conn.execute(
                """SELECT * FROM pr_watch_pg_launches
                   WHERE user_id=%s AND goal_id=%s""",
                (user_id, goal_id),
            ).fetchone()
        if row is None:
            raise AdmissionError("launch not found within user scope")
        return dict(row)

    def claim(
        self, *, user_id: str, goal_id: str, owner: str,
        timeout_seconds: int = 90,
    ) -> LaunchClaim:
        """Only the transaction that changes PENDING may call Temporal."""
        if (
            not _id(user_id) or not _id(goal_id) or not _id(owner)
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300
        ):
            raise AdmissionError("invalid launch claim")
        claimed_now = False
        with self._connection() as conn:
            row = conn.execute(
                """SELECT l.*,g.state AS goal_state
                   FROM pr_watch_pg_launches l
                   JOIN pr_watch_pg_goals g USING (user_id,goal_id)
                   WHERE l.user_id=%s AND l.goal_id=%s
                   FOR UPDATE OF l,g""",
                (user_id, goal_id),
            ).fetchone()
            if row is None:
                raise AdmissionError("launch not found within user scope")
            if row["state"] == "PENDING":
                if row["goal_state"] in ("RUNNING", "PAUSED"):
                    conn.execute(
                        """UPDATE pr_watch_pg_launches SET
                           state='IN_FLIGHT',owner=%s,generation=generation+1,
                           claim_deadline=clock_timestamp() +
                             (%s * interval '1 second'),
                           updated_at=clock_timestamp()
                           WHERE user_id=%s AND goal_id=%s""",
                        (owner, timeout_seconds, user_id, goal_id),
                    )
                    claimed_now = True
                else:
                    conn.execute(
                        """UPDATE pr_watch_pg_launches SET state='SKIPPED',
                           evidence='GOAL_INACTIVE',updated_at=clock_timestamp()
                           WHERE user_id=%s AND goal_id=%s""",
                        (user_id, goal_id),
                    )
            current = conn.execute(
                """SELECT state,owner,generation FROM pr_watch_pg_launches
                   WHERE user_id=%s AND goal_id=%s""",
                (user_id, goal_id),
            ).fetchone()
        return LaunchClaim(
            user_id=user_id, goal_id=goal_id, state=current["state"],
            owner=current["owner"], generation=current["generation"],
            claimed_now=claimed_now,
        )

    def finalize(
        self, *, claim: LaunchClaim, state: str,
    ) -> dict:
        """Fenced ACK/unknown write; late worker may not finalize expired claim."""
        if state not in ("ACKED", "UNKNOWN") or not claim.claimed_now:
            raise AdmissionError("invalid finalize request")
        evidence = "START_ACK" if state == "ACKED" else "START_UNCERTAIN"
        with self._connection() as conn:
            updated = conn.execute(
                """UPDATE pr_watch_pg_launches
                   SET state=%s,evidence=%s,updated_at=clock_timestamp()
                   WHERE user_id=%s AND goal_id=%s AND state='IN_FLIGHT'
                   AND owner=%s AND generation=%s
                   AND claim_deadline > clock_timestamp()
                   RETURNING user_id""",
                (
                    state, evidence, claim.user_id, claim.goal_id,
                    claim.owner, claim.generation,
                ),
            ).fetchone()
        if updated is None:
            raise AdmissionError("launch claim expired or fenced")
        return self.launch(user_id=claim.user_id, goal_id=claim.goal_id)

    def recover_expired(self) -> int:
        """Expired IN_FLIGHT -> UNKNOWN, never PENDING (no blind retry)."""
        with self._connection() as conn:
            result = conn.execute(
                """UPDATE pr_watch_pg_launches SET state='UNKNOWN',
                   evidence='EXPIRED_UNCERTAIN',updated_at=clock_timestamp()
                   WHERE state='IN_FLIGHT'
                     AND claim_deadline <= clock_timestamp()
                   RETURNING user_id"""
            ).fetchall()
        return len(result)

    def observe_existing(
        self, *, user_id: str, goal_id: str, workflow_status: str,
    ) -> dict:
        """Only caller with trusted positive Temporal Describe proof uses this."""
        if workflow_status not in _REMOTE_STATES:
            raise AdmissionError("untrusted Temporal workflow status")
        with self._connection() as conn:
            updated = conn.execute(
                """UPDATE pr_watch_pg_launches
                   SET state='OBSERVED',evidence='DESCRIBE_EXISTS',
                       workflow_status=%s,updated_at=clock_timestamp()
                   WHERE user_id=%s AND goal_id=%s
                   AND state IN ('IN_FLIGHT','UNKNOWN','ACKED','OBSERVED')
                   RETURNING user_id""",
                (workflow_status, user_id, goal_id),
            ).fetchone()
        if updated is None:
            raise AdmissionError("launch not eligible for reconciliation in user scope")
        return self.launch(user_id=user_id, goal_id=goal_id)

    def pending(self, *, limit: int = 100) -> list[dict]:
        """Trusted dispatcher listing only; never expose unscoped endpoint."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AdmissionError("invalid admission sweep limit")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT user_id,goal_id,state FROM pr_watch_pg_launches
                   WHERE state IN ('PENDING','UNKNOWN')
                   ORDER BY updated_at,user_id,goal_id LIMIT %s""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL Goal admission POC")
    parser.add_argument("command", choices=["init"])
    args = parser.parse_args()
    dsn = os.getenv("PR_WATCH_PG_DSN", "")
    try:
        admission = PostgresAdmission(dsn)
        if args.command == "init":
            admission.install_v1()
            print("PostgreSQL PR Watch admission v1 initialized")
    except AdmissionError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
