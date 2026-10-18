"""SIGKILL/WAL recovery acceptance against ONE GitHub Actions PostgreSQL service.

Destructive fault injection is *intentionally* restricted to the dedicated
ephemeral GitHub Actions job service. Refuses local/non-CI execution and
non-postgres:16 containers; never targets any arbitrary host/service.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from attention_plane import DeliveryState  # noqa: E402
from pr_watch_harness import PullSnapshot  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402
from pr_watch_service import LocalTokens, PilotHTTPServer, PilotService  # noqa: E402

TOKEN = "a" * 40
REPO = "o/prwatch"
USER = "crash-demo"
RECEIPT_USER = "crash-receipt"


def _cli(*args):
    return subprocess.run(
        ["docker", *args],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()


def _guard_environment():
    dsn = os.environ.get("PR_WATCH_PG_TEST_DSN", "")
    container = os.environ.get("PR_WATCH_PG_SERVICE_ID", "")
    if os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("CI") != "true":
        raise RuntimeError("destructive PG crash test requires GitHub Actions")
    if not re.fullmatch(r"[0-9a-f]{64}", container):
        raise RuntimeError("untrusted disposable PostgreSQL service ID")
    target = urlparse(dsn)
    if not (
        target.scheme in ("postgres", "postgresql")
        and target.hostname == "127.0.0.1"
        and target.port == 5432
        and target.path == "/prwatch"
    ):
        raise RuntimeError("refusing to operate on non-disposable test DSN")
    if _cli("inspect", "--format", "{{.Config.Image}}", container) != "postgres:16":
        raise RuntimeError("expected the GitHub Actions postgres:16 service")
    return dsn, container


def _http(port, path, *, authorization=False):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    headers = {"Authorization": "Bearer " + TOKEN} if authorization else {}
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def _started_at(dsn):
    with psycopg.connect(dsn, connect_timeout=2) as conn:
        return conn.execute("SELECT pg_postmaster_start_time()").fetchone()[0]


def _wait_for_db(dsn):
    for _ in range(100):
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            time.sleep(0.2)
    raise AssertionError("PostgreSQL did not recover after SIGKILL")


def _register(store, user, goal):
    return store.register(user_id=user, goal_id=goal, repo=REPO, number=180)


def _notify(store, user, goal, sha):
    _register(store, user, goal)
    result = store.observe(
        store.goal(user_id=user, goal_id=goal),
        PullSnapshot(head_sha=sha * 40, state="OPEN", ci="FAILURE"),
        now=datetime.now(timezone.utc),
    )
    assert result == "CI_BLOCKED"


def main():
    dsn, container = _guard_environment()
    store = PostgresWatchStore(dsn)
    store.validate_v2()
    # Earlier acceptance steps in the same disposable job legitimately
    # have records. Isolate this test by identity rather than TRUNCATE:
    # never destroy previous tests' persistence evidence.
    with psycopg.connect(dsn) as conn:
        assert conn.execute(
            """SELECT count(*) FROM pr_watch_pg_goals
               WHERE user_id IN (%s,%s)""",
            (USER, RECEIPT_USER),
        ).fetchone()[0] == 0
    old_epoch = _started_at(dsn)

    _register(store, USER, "acknowledged")
    claim = store.claim(user_id=USER, goal_id="acknowledged", owner="worker-a")
    assert claim.claimed_now
    assert store.finalize(claim=claim, state="ACKED")["state"] == "ACKED"
    paused = store.transition(
        user_id=USER, goal_id="acknowledged", action="pause",
        expected_version=0,
    )
    assert paused["version"] == 1
    store.record_control_signal(
        user_id=USER, goal_id="acknowledged", version=1,
        state="UNKNOWN", evidence="ACK_UNCERTAIN",
    )

    _register(store, USER, "uncertain-start")
    claim = store.claim(user_id=USER, goal_id="uncertain-start", owner="worker-b")
    assert store.finalize(claim=claim, state="UNKNOWN")["state"] == "UNKNOWN"

    _register(store, USER, "inflight-start")
    assert store.claim(
        user_id=USER, goal_id="inflight-start",
        owner="worker-c", timeout_seconds=1,
    ).claimed_now

    _notify(store, USER, "uncertain-delivery", "a")
    leases = store.claim_due(
        user_id=USER, owner="sender-a", lease_seconds=1,
    )
    assert len(leases) == 1

    _notify(store, RECEIPT_USER, "accepted-delivery", "b")
    receipt_lease = store.claim_due(
        user_id=RECEIPT_USER, owner="sender-b",
    )[0]
    store.complete_delivery(
        receipt_lease, state=DeliveryState.DELIVERED, provider="telegram",
        remote_message_id="180", recipient_digest="c" * 64,
    )
    before = {
        "launch": store.launch(user_id=USER, goal_id="acknowledged"),
        "control": store.control(user_id=USER, goal_id="acknowledged", version=1),
        "events": store.list_events(user_id=USER),
        "receipt": store.receipt(
            user_id=RECEIPT_USER, outbox_id=receipt_lease.outbox_id,
        ),
    }
    # Open transaction remains uncommitted until SIGKILL. PostgreSQL
    # recovery must remove both Goal and Launch, not partially commit them.
    pending = psycopg.connect(dsn, connect_timeout=2)
    pending.execute(
        """INSERT INTO pr_watch_pg_goals
           (user_id,goal_id,repo,number,state)
           VALUES (%s,%s,%s,180,'RUNNING')""",
        (USER, "not-committed", REPO),
    )
    pending.execute(
        """INSERT INTO pr_watch_pg_launches(user_id,goal_id)
           VALUES (%s,%s)""",
        (USER, "not-committed"),
    )

    service = PilotService(
        None, LocalTokens({USER: TOKEN}),
        storage_backend="postgres", pg_dsn=dsn,
    )
    server = PilotHTTPServer(("127.0.0.1", 0), service)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    killed = False
    try:
        port = server.server_port
        assert _http(port, "/readyz") == (200, {"status": "ready"})
        assert _http(port, "/healthz") == (200, {"status": "ok"})
        assert _http(port, "/api/goals/acknowledged", authorization=True)[0] == 200

        # Only the validated single disposable GitHub PG service is targeted.
        # SIGKILL, not docker stop/SIGTERM, forces real WAL crash recovery.
        _cli("kill", "--signal=SIGKILL", container)
        killed = True
        # The API process remains alive, but DB-dependent readiness
        # explicitly becomes 503 without exposing a DSN or SQL exception.
        assert _http(port, "/healthz") == (200, {"status": "ok"})
        status, response = _http(port, "/readyz")
        assert status == 503 and response == {"status": "unavailable"}
        _cli("start", container)
        killed = False
        _wait_for_db(dsn)
        assert _started_at(dsn) > old_epoch
        store.validate_v2()
        assert _http(port, "/readyz") == (200, {"status": "ready"})
        assert _http(port, "/api/goals/acknowledged", authorization=True)[1][
            "goal"
        ]["state"] == "PAUSED"

        # An interrupted open client transaction may be lost entirely,
        # whereas previously committed controls/events/receipts survive.
        with psycopg.connect(dsn) as conn:
            assert conn.execute(
                """SELECT count(*) FROM pr_watch_pg_goals
                   WHERE user_id=%s AND goal_id='not-committed'""",
                (USER,),
            ).fetchone()[0] == 0
        assert store.launch(user_id=USER, goal_id="acknowledged") == before["launch"]
        assert store.control(
            user_id=USER, goal_id="acknowledged", version=1,
        ) == before["control"]
        assert store.list_events(user_id=USER) == before["events"]
        assert store.receipt(
            user_id=RECEIPT_USER, outbox_id=receipt_lease.outbox_id,
        ) == before["receipt"]

        assert store.launch(user_id=USER, goal_id="uncertain-start")["state"] == "UNKNOWN"
        assert store.claim(
            user_id=USER, goal_id="uncertain-start", owner="no-replay",
        ).claimed_now is False

        # Expiration is conservative even after a physical server crash:
        # no retry of possibly accepted remote start or notification.
        # The database can restart faster than the one-second leases.
        # Wait for PostgreSQL server clock, not assumptions about Docker time.
        for _ in range(30):
            store.recover_expired()
            if store.launch(
                user_id=USER, goal_id="inflight-start",
            )["state"] == "UNKNOWN":
                break
            time.sleep(0.1)
        assert store.launch(user_id=USER, goal_id="inflight-start")["state"] == "UNKNOWN"
        assert not store.claim(
            user_id=USER, goal_id="inflight-start", owner="new-worker",
        ).claimed_now
        for _ in range(30):
            store.recover_expired_deliveries(user_id=USER)
            if store.delivery_counts(user_id=USER) == {"UNKNOWN": 1}:
                break
            time.sleep(0.1)
        assert store.delivery_counts(user_id=USER) == {"UNKNOWN": 1}
        assert store.claim_due(user_id=USER, owner="new-sender") == []
        assert store.delivery_counts(user_id=RECEIPT_USER) == {"DELIVERED": 1}
        assert _register(store, USER, "acknowledged")["version"] == 1
        print("PASS: PostgreSQL SIGKILL/WAL recovery + HTTP readiness + durable evidence")
    finally:
        if killed:
            # Restore only our dedicated service even after an assertion
            # failure, so GitHub runner cleanup isn't blocked.
            _cli("start", container)
        try:
            pending.close()
        except psycopg.Error:
            pass
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()
