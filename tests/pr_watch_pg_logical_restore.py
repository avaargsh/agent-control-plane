"""PostgreSQL 16 logical backup/restore in a separate disposable database.

Runs ONLY as the final stages of a disposable GitHub Actions PostgreSQL 16
service; never overwrites the source database, never suggests PITR coverage.
"""
from __future__ import annotations

import os
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pr_watch_pg_server_crash import _guard_environment  # noqa: E402
from attention_plane import DeliveryState  # noqa: E402
from pr_watch_harness import PullSnapshot  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402

USER = "backup-acceptance"
RESTORE_DB = "prwatch_restore_acceptance"
DUMP = "/tmp/pr_watch_pg_ci_snapshot.dump"


def _run(container, *args):
    return subprocess.run(
        ["docker", "exec", container, *args],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout


def main():
    dsn, container = _guard_environment()
    store = PostgresWatchStore(dsn)
    store.validate_v2()
    store.register(user_id=USER, goal_id="goal-1", repo="o/repo", number=181)
    claim = store.claim(user_id=USER, goal_id="goal-1", owner="backup-worker")
    assert claim.claimed_now
    store.finalize(claim=claim, state="ACKED")
    store.transition(
        user_id=USER, goal_id="goal-1", action="pause", expected_version=0,
    )
    store.record_control_signal(
        user_id=USER, goal_id="goal-1", version=1,
        state="UNKNOWN", evidence="ACK_UNCERTAIN",
    )
    store.register(user_id=USER, goal_id="notice", repo="o/repo", number=181)
    store.observe(
        store.goal(user_id=USER, goal_id="notice"),
        PullSnapshot(head_sha="d" * 40, state="OPEN", ci="FAILURE"),
        now=datetime.now(timezone.utc),
    )
    leases = store.claim_due(user_id=USER, owner="telegram-dispatch")
    assert len(leases) == 1
    store.complete_delivery(
        leases[0], state=DeliveryState.DELIVERED, provider="telegram",
        remote_message_id="181", recipient_digest="e" * 64,
    )
    pre = {
        "goal": store.goal(user_id=USER, goal_id="goal-1"),
        "launch": store.launch(user_id=USER, goal_id="goal-1"),
        "control": store.control(user_id=USER, goal_id="goal-1", version=1),
        "events": store.list_events(user_id=USER),
        "receipt": store.receipt(user_id=USER, outbox_id=leases[0].outbox_id),
        "counts": store.delivery_counts(user_id=USER),
    }

    # Dump source DB as a CUSTOM-format PG16 archive within the *validated*
    # dedicated CI container. Restore into a distinct new database.
    _run(container, "pg_dump", "-U", "postgres", "-F", "c",
         "-f", DUMP, "prwatch")
    _run(container, "createdb", "-U", "postgres", RESTORE_DB)
    try:
        _run(container, "pg_restore", "-U", "postgres", "--no-owner",
             "--no-acl", "-d", RESTORE_DB, DUMP)
        restored_dsn = make_conninfo(dsn, dbname=RESTORE_DB)
        replica = PostgresWatchStore(restored_dsn)
        replica.validate_v2()
        assert replica.goal(user_id=USER, goal_id="goal-1") == pre["goal"]
        assert replica.launch(user_id=USER, goal_id="goal-1") == pre["launch"]
        assert replica.control(user_id=USER, goal_id="goal-1", version=1) == pre["control"]
        assert replica.list_events(user_id=USER) == pre["events"]
        assert replica.receipt(user_id=USER, outbox_id=leases[0].outbox_id) == pre["receipt"]
        assert replica.delivery_counts(user_id=USER) == pre["counts"]
        assert replica.claim(
            user_id=USER, goal_id="goal-1", owner="new-worker",
        ).claimed_now is False
        assert replica.claim_due(user_id=USER, owner="new-sender") == []

        # A write AFTER the dump must be absent from the restored snapshot.
        store.register(user_id=USER, goal_id="after-backup", repo="o/repo", number=181)
        assert store.goal(user_id=USER, goal_id="after-backup")["state"] == "RUNNING"
        try:
            replica.goal(user_id=USER, goal_id="after-backup")
        except ValueError as exc:
            assert "not found" in str(exc)
        else:
            raise AssertionError("restored snapshot incorrectly includes later data")
        print("PASS: pg_dump/pg_restore point-in-time logical snapshot and state fidelity")
    finally:
        # Only the newly created disposable clone can be removed.
        _run(container, "dropdb", "-U", "postgres", "--if-exists", RESTORE_DB)


if __name__ == "__main__":
    main()
