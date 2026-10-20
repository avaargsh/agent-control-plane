"""Operator-only transactional PostgreSQL schema migrator for PR Watch Pilot.

Version 1 = admission Goal/Launch. Version 2 = observations/control/Attention.
Version 3 = idempotent prepared controls, a separate durable CAS request ledger.
Legacy *complete* v1/v2 schemas can be adopted without data mutation.
Do not run against production before backup/review and application quiescence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass

from psycopg import connect
from psycopg.rows import dict_row

from pr_watch_pg_admission import SCHEMA_V1
from pr_watch_pg_store import SCHEMA_V2

# Existing published DDL is pinned by its audit digest on first installation.
# Modifying a previously recorded version will fail closed on next read.
SCHEMA_V3 = """
-- Extend the V1/V2 ledger check without modifying recorded checksums.
ALTER TABLE pr_watch_pg_schema_migrations
  DROP CONSTRAINT IF EXISTS pr_watch_pg_schema_migrations_version_check;
ALTER TABLE pr_watch_pg_schema_migrations
  ADD CONSTRAINT pr_watch_pg_schema_migrations_version_check
    CHECK(version IN (1,2,3));
CREATE TABLE IF NOT EXISTS pr_watch_pg_control_requests (
  user_id TEXT NOT NULL,
  goal_id TEXT NOT NULL,
  request_id TEXT NOT NULL CHECK(
    length(request_id)=32 AND request_id !~ '[^0-9a-f]'
  ),
  action TEXT NOT NULL CHECK(action IN ('pause','resume','cancel')),
  expected_version BIGINT NOT NULL CHECK(expected_version >= 0),
  state TEXT NOT NULL DEFAULT 'PREPARED'
    CHECK(state IN ('PREPARED','COMMITTED')),
  committed_version BIGINT,
  PRIMARY KEY(user_id,request_id),
  FOREIGN KEY(user_id,goal_id) REFERENCES pr_watch_pg_goals(user_id,goal_id),
  CHECK(
    (state='PREPARED' AND committed_version IS NULL)
    OR (state='COMMITTED' AND committed_version=expected_version+1)
  )
);
CREATE INDEX IF NOT EXISTS pr_watch_pg_control_requests_user
  ON pr_watch_pg_control_requests(user_id,goal_id,expected_version);
"""
SCRIPTS = {1: SCHEMA_V1, 2: SCHEMA_V2, 3: SCHEMA_V3}
LEDGER = "pr_watch_pg_schema_migrations"
LOCK_ID = 657805283175221
V1 = {
    "pr_watch_pg_goals": {
        "user_id", "goal_id", "repo", "number", "state", "version",
    },
    "pr_watch_pg_launches": {
        "user_id", "goal_id", "state", "owner", "generation",
        "claim_deadline", "evidence", "workflow_status", "updated_at",
    },
}
V2 = {
    "pr_watch_pg_goals": {"phase", "signature"},
    "pr_watch_pg_controls": {
        "user_id", "goal_id", "version", "action",
        "signal_state", "checked_at", "evidence",
    },
    "pr_watch_pg_events": {
        "cursor", "user_id", "goal_id", "event_id", "kind",
        "summary", "dedupe_key", "occurred_at", "emitted",
    },
    "pr_watch_pg_attention_decisions": {
        "user_id", "event_id", "goal_id", "disposition", "reason",
        "requires_approval", "notify_after",
    },
    "pr_watch_pg_deliveries": {
        "outbox_id", "user_id", "goal_id", "event_id", "dedupe_key",
        "summary", "due_at", "state", "owner", "generation", "expires_at",
        "delivered_at", "reconciliation_evidence",
    },
    "pr_watch_pg_delivery_receipts": {
        "outbox_id", "user_id", "provider", "recipient_digest",
        "remote_message_id", "accepted_at", "owner", "generation",
    },
}
V3 = {"pr_watch_pg_control_requests": {
    "user_id", "goal_id", "request_id", "action", "expected_version",
    "state", "committed_version",
}}
INDEXES = {
    1: ("pr_watch_pg_launches_pending",),
    2: ("pr_watch_pg_events_cursor", "pr_watch_pg_deliveries_due"),
    3: ("pr_watch_pg_control_requests_user",),
}
# Required minimum number of primary, unique and foreign key constraints.
KEYS = {
    "pr_watch_pg_goals": (1, 0, 0),
    "pr_watch_pg_launches": (1, 0, 1),
    "pr_watch_pg_controls": (1, 0, 1),
    "pr_watch_pg_control_requests": (1, 0, 1),
    "pr_watch_pg_events": (1, 1, 1),
    "pr_watch_pg_attention_decisions": (1, 0, 0),
    "pr_watch_pg_deliveries": (1, 1, 1),
    "pr_watch_pg_delivery_receipts": (1, 1, 1),
}
DOWN_V2 = """
DROP TABLE pr_watch_pg_delivery_receipts;
DROP TABLE pr_watch_pg_deliveries;
DROP TABLE pr_watch_pg_attention_decisions;
DROP TABLE pr_watch_pg_events;
DROP TABLE pr_watch_pg_controls;
ALTER TABLE pr_watch_pg_goals DROP COLUMN phase;
ALTER TABLE pr_watch_pg_goals DROP COLUMN signature;
"""


class MigrationError(RuntimeError):
    """Schema is unknown, drifted, incompatible, or unsafe to downgrade."""


@dataclass(frozen=True)
class MigrationState:
    version: int
    adopted: bool = False


def _digest(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


class PostgresMigrations:
    def __init__(self, dsn: str) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise MigrationError("explicit PR_WATCH_PG_DSN required")
        self.dsn = dsn

    def _connect(self):
        return connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    @staticmethod
    def _columns(conn, table: str) -> set[str]:
        rows = conn.execute(
            """SELECT a.attname FROM pg_attribute a
               WHERE a.attrelid=to_regclass(%s) AND a.attnum>0
                 AND NOT a.attisdropped""", (table,),
        ).fetchall()
        return {row["attname"] for row in rows}

    @staticmethod
    def _exists(conn, object_name: str) -> bool:
        return conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL AS present", (object_name,),
        ).fetchone()["present"]

    @staticmethod
    def _constraints(conn, table: str) -> tuple[int, int, int]:
        rows = conn.execute(
            """SELECT contype, count(*) AS n FROM pg_constraint
               WHERE conrelid=to_regclass(%s) AND contype IN ('p','u','f')
               GROUP BY contype""", (table,),
        ).fetchall()
        by_type = {row["contype"]: row["n"] for row in rows}
        return tuple(by_type.get(kind, 0) for kind in ("p", "u", "f"))

    def _check_shape(self, conn, version: int) -> None:
        expected = dict(V1)
        if version >= 2:
            expected["pr_watch_pg_goals"] = (
                expected["pr_watch_pg_goals"] | V2["pr_watch_pg_goals"]
            )
            expected.update({
                name: columns for name, columns in V2.items()
                if name != "pr_watch_pg_goals"
            })
        if version >= 3:
            expected.update(V3)
        for table, columns in expected.items():
            if not self._exists(conn, table):
                raise MigrationError("missing required schema table: " + table)
            if not columns <= self._columns(conn, table):
                raise MigrationError("missing required columns: " + table)
            found = self._constraints(conn, table)
            required = KEYS[table]
            if any(f < r for f, r in zip(found, required, strict=True)):
                raise MigrationError("missing required keys: " + table)
        for ver in range(1, version + 1):
            for index in INDEXES[ver]:
                if not self._exists(conn, index):
                    raise MigrationError("missing required index: " + index)

    def _legacy_version(self, conn) -> int:
        names = set(V1) | set(V2) | set(V3)
        present = {name for name in names if self._exists(conn, name)}
        if not present:
            return 0
        if not set(V1) <= present:
            raise MigrationError("partial legacy V1 schema; manual review required")
        complete_v2 = set(V2) - {"pr_watch_pg_goals"}
        if complete_v2 <= present:
            if set(V3) & present:
                raise MigrationError("unversioned V3 schema requires manual review")
            self._check_shape(conn, 2)
            return 2
        if present != set(V1):
            raise MigrationError("partial legacy V2 schema; manual review required")
        if V2["pr_watch_pg_goals"] & self._columns(conn, "pr_watch_pg_goals"):
            raise MigrationError("partially applied V2 columns; manual review required")
        self._check_shape(conn, 1)
        return 1

    def _current(self, conn, *, permit_adoption: bool) -> MigrationState:
        if not self._exists(conn, LEDGER):
            legacy = self._legacy_version(conn)
            if legacy and not permit_adoption:
                raise MigrationError("unversioned legacy schema requires explicit adoption")
            return MigrationState(legacy, adopted=bool(legacy))
        rows = conn.execute(
            "SELECT version,checksum FROM " + LEDGER + " ORDER BY version"
        ).fetchall()
        versions = [r["version"] for r in rows]
        if versions != list(range(1, len(rows) + 1)) or any(
            r["version"] not in SCRIPTS
            or r["checksum"] != _digest(SCRIPTS[r["version"]])
            for r in rows
        ):
            raise MigrationError("migration ledger mismatch or SQL checksum drift")
        if rows:
            self._check_shape(conn, rows[-1]["version"])
        elif self._legacy_version(conn):
            # Even an empty ledger cannot conceal an existing untracked schema.
            raise MigrationError("schema exists without migration ledger history")
        return MigrationState(len(rows))

    @staticmethod
    def _create_ledger(conn) -> None:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pr_watch_pg_schema_migrations (
                 version INTEGER PRIMARY KEY CHECK (version IN (1,2,3)),
                 checksum CHAR(64) NOT NULL,
                 applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
               )"""
        )

    def status(self) -> MigrationState:
        """Read-only, no implicit DDL, no legacy adoption."""
        with self._connect() as conn:
            return self._current(conn, permit_adoption=False)

    def require_version(self, version: int) -> None:
        state = self.status()
        if state.version != version:
            raise MigrationError(
                f"PostgreSQL schema version {version} required; found {state.version}"
            )

    def require_minimum_version(self, version: int) -> None:
        state = self.status()
        if state.version < version:
            raise MigrationError(
                f"PostgreSQL schema version {version}+ required; found {state.version}"
            )

    def upgrade(self, target: int = 2, *, adopt_legacy: bool = False) -> MigrationState:
        """Single transaction; advisory lock serializes participating installers.

        Operator must quiesce application writers; the lock alone does not
        coordinate clients that never acquire this migration lock.
        """
        if type(target) is not int or target not in SCRIPTS:
            raise MigrationError("target version must be 1, 2 or 3")
        with self._connect() as conn:
            conn.execute("SET LOCAL lock_timeout = '8s'")
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            initial = self._current(conn, permit_adoption=adopt_legacy)
            self._create_ledger(conn)
            if initial.adopted:
                for version in range(1, initial.version + 1):
                    conn.execute(
                        """INSERT INTO pr_watch_pg_schema_migrations
                           (version,checksum) VALUES (%s,%s)""",
                        (version, _digest(SCRIPTS[version])),
                    )
            for version in range(initial.version + 1, target + 1):
                conn.execute(SCRIPTS[version])
                self._check_shape(conn, version)
                conn.execute(
                    """INSERT INTO pr_watch_pg_schema_migrations
                       (version,checksum) VALUES (%s,%s)""",
                    (version, _digest(SCRIPTS[version])),
                )
            final = self._current(conn, permit_adoption=False)
        # A request to install old version never downgrades a newer schema.
        return MigrationState(final.version, adopted=initial.adopted)

    def rollback_empty_v2(self, *, confirm_empty: bool = False) -> MigrationState:
        """Destructive schema downgrade only if EVERY pilot table has zero rows.

        Never an automatic application startup action. There is deliberately
        no populated-data downgrade; use backups and an offline migration plan.
        """
        if not confirm_empty:
            raise MigrationError("explicit confirm_empty required for rollback")
        with self._connect() as conn:
            conn.execute("SET LOCAL lock_timeout = '8s'")
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            current = self._current(conn, permit_adoption=False)
            if current.version != 2:
                raise MigrationError("only version 2 -> 1 rollback supported")
            # Blocks all concurrent inserts/updates during the empty check and
            # DROP, including writers that do not use the advisory lock.
            names = sorted(set(V1) | set(V2))
            conn.execute(
                "LOCK TABLE " + ", ".join(names) + " IN ACCESS EXCLUSIVE MODE"
            )
            for table in names:
                if conn.execute("SELECT EXISTS(SELECT 1 FROM " + table + ") AS any").fetchone()["any"]:
                    raise MigrationError("nonempty table prevents schema rollback: " + table)
            conn.execute(DOWN_V2)
            conn.execute(
                "DELETE FROM pr_watch_pg_schema_migrations WHERE version=2"
            )
            self._check_shape(conn, 1)
        return MigrationState(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Operator-only PR Watch PG migrations")
    parser.add_argument("action", choices=("status", "upgrade", "rollback-empty-v2"))
    parser.add_argument("--to", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--adopt-legacy", action="store_true")
    parser.add_argument("--confirm-empty", action="store_true")
    args = parser.parse_args()
    tool = PostgresMigrations(os.getenv("PR_WATCH_PG_DSN", ""))
    try:
        if args.action == "status":
            current = tool.status()
        elif args.action == "upgrade":
            current = tool.upgrade(args.to, adopt_legacy=args.adopt_legacy)
        else:
            current = tool.rollback_empty_v2(confirm_empty=args.confirm_empty)
    except MigrationError as exc:
        parser.error(str(exc))
    print(json.dumps({
        "schema_version": current.version, "legacy_adopted": current.adopted,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
