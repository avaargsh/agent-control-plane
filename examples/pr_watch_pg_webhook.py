"""Explicit PostgreSQL webhook inbox extension for the PR Watch V2 pilot.

Operator-installed, versioned independently from frozen V1/V2 migrations.
The webhook never grants authority or writes to GitHub. No request-time DDL.
"""
from __future__ import annotations

import hashlib
from uuid import UUID

from psycopg import connect
from psycopg.rows import dict_row

from pr_watch_pg_store import PostgresWatchStore
from pr_watch_webhook_envelope import (
    WebhookRejected, verified_delivery, wake_signal_id,
)

SCHEMA = """
CREATE TABLE pr_watch_pg_webhook_wakes (
  user_id TEXT NOT NULL,
  goal_id TEXT NOT NULL,
  delivery UUID NOT NULL,
  payload_sha256 CHAR(64) NOT NULL,
  signal_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('IN_FLIGHT','ACKED','UNKNOWN')),
  PRIMARY KEY (user_id, goal_id, delivery),
  FOREIGN KEY (user_id, goal_id)
    REFERENCES pr_watch_pg_goals(user_id, goal_id)
);
"""
LEDGER_SQL = """
CREATE TABLE pr_watch_pg_webhook_schema (
  version INTEGER PRIMARY KEY CHECK (version = 1),
  checksum CHAR(64) NOT NULL
);
"""
CHECKSUM = hashlib.sha256(SCHEMA.encode("utf-8")).hexdigest()
ADVISORY_LOCK = 657805283175229


class PostgresWebhookSchema:
    """Separate extension ledger, never modifies existing V1/V2 DDL digests."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _connect(self):
        return connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    @staticmethod
    def _verify(conn) -> None:
        schema = conn.execute(
            "SELECT version, checksum FROM pr_watch_pg_webhook_schema"
        ).fetchall()
        if len(schema) != 1 or schema[0] != {
            "version": 1, "checksum": CHECKSUM,
        }:
            raise ValueError("webhook schema version/checksum mismatch")
        cols = conn.execute(
            """SELECT attname, format_type(atttypid,atttypmod) AS type,
                      attnotnull AS required
               FROM pg_attribute
               WHERE attrelid=to_regclass('pr_watch_pg_webhook_wakes')
                 AND attnum>0 AND NOT attisdropped"""
        ).fetchall()
        # A table with the right column names but wrong types/nullable fields
        # cannot safely honor the durable claim and scoped-key contract.
        expected = {
            "user_id": "text", "goal_id": "text", "delivery": "uuid",
            "payload_sha256": "character(64)", "signal_id": "text",
            "state": "text",
        }
        if ({r["attname"]: r["type"] for r in cols} != expected
                or not all(r["required"] for r in cols)):
            raise ValueError("webhook inbox columns/type/nullability mismatch")
        constraints = conn.execute(
            """SELECT contype, pg_get_constraintdef(oid) AS definition
               FROM pg_constraint
               WHERE conrelid=to_regclass('pr_watch_pg_webhook_wakes')"""
        ).fetchall()
        pk = [r["definition"] for r in constraints if r["contype"] == "p"]
        fks = [r["definition"] for r in constraints if r["contype"] == "f"]
        checks = [r["definition"] for r in constraints if r["contype"] == "c"]
        if pk != ["PRIMARY KEY (user_id, goal_id, delivery)"]:
            raise ValueError("webhook inbox scoped primary key drift")
        if not any(
            fk.startswith(
                "FOREIGN KEY (user_id, goal_id) REFERENCES "
                "pr_watch_pg_goals(user_id, goal_id)"
            ) or fk.startswith(
                "FOREIGN KEY (user_id, goal_id) REFERENCES "
                "public.pr_watch_pg_goals(user_id, goal_id)"
            ) for fk in fks
        ):
            raise ValueError("webhook inbox goal foreign key drift")
        if not checks or not any(
            "IN_FLIGHT" in check and "ACKED" in check
            and "UNKNOWN" in check and "state" in check
            for check in checks
        ):
            raise ValueError("webhook inbox state restriction drift")

    def install(self) -> None:
        """Explicit operator-only install; application startup never calls it."""
        PostgresWatchStore(self.dsn).validate_v2()
        with self._connect() as conn:
            conn.execute("SET LOCAL lock_timeout = '8s'")
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK,))
            exists = conn.execute(
                """SELECT to_regclass('pr_watch_pg_webhook_schema') IS NOT NULL
                      AS ledger, to_regclass('pr_watch_pg_webhook_wakes')
                      IS NOT NULL AS inbox"""
            ).fetchone()
            if not exists["ledger"] and not exists["inbox"]:
                conn.execute(LEDGER_SQL)
                conn.execute(SCHEMA)
                conn.execute(
                    """INSERT INTO pr_watch_pg_webhook_schema(version,checksum)
                       VALUES (1,%s)""", (CHECKSUM,),
                )
            elif not (exists["ledger"] and exists["inbox"]):
                raise ValueError("partial webhook schema; manual recovery required")
            self._verify(conn)

    def validate(self) -> None:
        PostgresWatchStore(self.dsn).validate_v2()
        with self._connect() as conn:
            self._verify(conn)


class PostgresWebhookIngress:
    """Atomically claim one Signal attempt per delivery using PG row locks."""

    def __init__(
        self, *, store: PostgresWatchStore, user_id: str, goal_id: str,
        secret: bytes, signal,
    ) -> None:
        if type(secret) is not bytes or len(secret) < 32 or not callable(signal):
            raise ValueError("webhook secret >=32 bytes and signal required")
        PostgresWebhookSchema(store._dsn).validate()
        goal = store.goal(user_id=user_id, goal_id=goal_id)
        self.store = store
        self.user_id, self.goal_id = user_id, goal_id
        self.repo, self.number = goal["repo"], goal["number"]
        self._secret, self._signal = secret, signal

    def _db(self):
        return self.store._connection()

    def accept(self, *, signature: str, event: str, delivery: str,
               body: bytes) -> dict:
        delivery = verified_delivery(
            secret=self._secret, repo=self.repo, number=self.number,
            signature=signature, event=event, delivery=delivery, body=body,
        )
        if delivery is None:
            return {"status": "ignored"}
        digest = hashlib.sha256(body).hexdigest()
        signal_id = wake_signal_id(self.user_id, self.goal_id, delivery)
        with self._db() as db:
            # Serializes concurrent deliveries for this Goal. Claim commits
            # before RPC. Distinct users, even same delivery, remain isolated.
            row = db.execute(
                """SELECT repo,number,state FROM pr_watch_pg_goals
                   WHERE user_id=%s AND goal_id=%s FOR UPDATE""",
                (self.user_id, self.goal_id),
            ).fetchone()
            if (row is None or row["repo"] != self.repo
                    or row["number"] != self.number):
                return {"status": "ignored"}
            prior = db.execute(
                """SELECT payload_sha256,state FROM pr_watch_pg_webhook_wakes
                   WHERE user_id=%s AND goal_id=%s AND delivery=%s""",
                (self.user_id, self.goal_id, delivery),
            ).fetchone()
            if prior:
                if prior["payload_sha256"] != digest:
                    raise WebhookRejected(409, "delivery identity reused")
                return {"status": "duplicate", "signal_state": prior["state"]}
            if row["state"] != "RUNNING":
                return {"status": "ignored"}
            db.execute(
                """INSERT INTO pr_watch_pg_webhook_wakes
                   (user_id,goal_id,delivery,payload_sha256,signal_id,state)
                   VALUES (%s,%s,%s,%s,%s,'IN_FLIGHT')""",
                (self.user_id, self.goal_id, delivery, digest, signal_id),
            )
        try:
            self._signal(signal_id)
            state = "ACKED"
        except Exception:
            state = "UNKNOWN"
        with self._db() as db:
            db.execute(
                """UPDATE pr_watch_pg_webhook_wakes SET state=%s
                   WHERE user_id=%s AND goal_id=%s AND delivery=%s
                   AND state='IN_FLIGHT'""",
                (state, self.user_id, self.goal_id, delivery),
            )
        return {"status": "accepted", "signal_state": state}

    def reconcile(self, *, delivery: str, prove) -> dict:
        """Only Temporal History positive proof can promote UNKNOWN to ACKED.

        Absence, partial History or errors never allow a resend.
        """
        try:
            canonical = str(UUID(delivery))
        except (ValueError, AttributeError, TypeError) as exc:
            raise WebhookRejected(400, "invalid delivery identity") from exc
        if canonical != delivery or not callable(prove):
            raise WebhookRejected(400, "invalid reconciliation request")
        with self._db() as db:
            row = db.execute(
                """SELECT state,signal_id FROM pr_watch_pg_webhook_wakes
                   WHERE user_id=%s AND goal_id=%s AND delivery=%s""",
                (self.user_id, self.goal_id, delivery),
            ).fetchone()
        if row is None:
            raise WebhookRejected(404, "delivery not found within goal scope")
        if row["state"] == "ACKED":
            return {"signal_state": "ACKED"}
        try:
            proved = prove(row["signal_id"]) is True
        except Exception:
            proved = False
        if not proved:
            return {"signal_state": row["state"]}
        with self._db() as db:
            db.execute(
                """UPDATE pr_watch_pg_webhook_wakes SET state='ACKED'
                   WHERE user_id=%s AND goal_id=%s AND delivery=%s
                     AND signal_id=%s AND state IN ('IN_FLIGHT','UNKNOWN')""",
                (self.user_id, self.goal_id, delivery, row["signal_id"]),
            )
            current = db.execute(
                """SELECT state FROM pr_watch_pg_webhook_wakes
                   WHERE user_id=%s AND goal_id=%s AND delivery=%s""",
                (self.user_id, self.goal_id, delivery),
            ).fetchone()
        if current is None:
            raise WebhookRejected(404, "delivery not found within goal scope")
        return {"signal_state": current["state"]}


def main() -> None:
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Operator-only PostgreSQL webhook extension schema"
    )
    parser.add_argument("action", choices=("install", "validate"))
    args = parser.parse_args()
    dsn = os.getenv("PR_WATCH_PG_DSN", "")
    if not dsn:
        parser.error("PR_WATCH_PG_DSN is required")
    schema = PostgresWebhookSchema(dsn)
    try:
        if args.action == "install":
            schema.install()
        else:
            schema.validate()
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print("PostgreSQL webhook extension: " + args.action + " verified")


if __name__ == "__main__":
    main()
