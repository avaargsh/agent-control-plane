"""Real OS-process crash/fencing acceptance for opt-in PostgreSQL PR Watch.

All processes are children spawned by this test. Tests require an EXCLUSIVE,
DISPOSABLE PR_WATCH_PG_TEST_DSN; the fixture truncates pilot tables. No mock
database, no production Telegram/GitHub calls, no subprocess daemon left behind.
"""
from __future__ import annotations

import http.client
import json
import multiprocessing
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest

psycopg = pytest.importorskip("psycopg")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from pr_watch_harness import PullSnapshot  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")
TOKENS = {"alice": "a" * 40, "bob": "b" * 40}
SPAWN = multiprocessing.get_context("spawn")


@pytest.fixture(scope="module")
def db():
    if not DSN:
        pytest.skip("exclusive, disposable PR_WATCH_PG_TEST_DSN is required")
    store = PostgresWatchStore(DSN)
    store.install_v2()
    with psycopg.connect(DSN) as conn:
        conn.execute("TRUNCATE pr_watch_pg_goals CASCADE")
    yield store


def _serve_api(dsn, ready, stop):
    # This subprocess imports psycopg and instantiates its OWN connections.
    # It MUST never receive a connection or socket from the parent process.
    from threading import Thread

    from pr_watch_service import LocalTokens, PilotHTTPServer, PilotService

    service = PilotService(
        None, LocalTokens(TOKENS),
        storage_backend="postgres", pg_dsn=dsn,
    )
    server = PilotHTTPServer(("127.0.0.1", 0), service)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    ready.put(server.server_port)
    try:
        stop.wait(35)
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


@contextmanager
def _api_child(dsn):
    ready, stop = SPAWN.Queue(), SPAWN.Event()
    proc = SPAWN.Process(target=_serve_api, args=(dsn, ready, stop))
    proc.start()
    try:
        port = ready.get(timeout=20)
        assert proc.is_alive()
        yield proc, port
    finally:
        stop.set()
        proc.join(timeout=8)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)
        ready.close()
        ready.join_thread()


def _http(port, method, path, *, who="alice", body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Authorization": "Bearer " + TOKENS[who]}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return response.status, json.loads(raw)
    finally:
        conn.close()


def _claim_launch(dsn, goal, owner, gate, results, linger):
    store = PostgresWatchStore(dsn)
    gate.wait(timeout=20)
    claim = store.claim(
        user_id="alice", goal_id=goal, owner=owner,
        timeout_seconds=1 if linger else 90,
    )
    results.put((owner, claim.claimed_now, claim.state, claim.generation))
    if linger and claim.claimed_now:
        # Simulate an API process killed after durable admission and before
        # recording any outcome. Parent terminates this exact child PID.
        time.sleep(30)


def _claim_notification(dsn, goal, owner, gate, results, linger):
    store = PostgresWatchStore(dsn)
    gate.wait(timeout=20)
    leases = store.claim_due(
        user_id="alice", owner=owner, limit=1,
        lease_seconds=1 if linger else 90,
    )
    results.put((owner, len(leases), leases[0].outbox_id if leases else None))
    if linger and leases:
        time.sleep(30)


def _uncommitted_insert(dsn, ready):
    # Do NOT commit the in-flight write. Termination closes this process's
    # PostgreSQL session; server must roll back the entire open transaction.
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """INSERT INTO pr_watch_pg_goals
               (user_id, goal_id, repo, number, state)
               VALUES ('alice','killed-transaction','o/repo',1,'RUNNING')"""
        )
        conn.execute(
            """INSERT INTO pr_watch_pg_launches (user_id,goal_id)
               VALUES ('alice','killed-transaction')"""
        )
        ready.put(True)
        time.sleep(30)


def _join_or_kill(proc):
    proc.join(timeout=8)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=5)
    assert not proc.is_alive()


def test_two_real_api_processes_share_goal_and_cas_without_duplicate_intents(db):
    with _api_child(DSN) as (first, a), _api_child(DSN) as (second, b):
        gate = Barrier(2)

        def register(port):
            gate.wait(timeout=10)
            return _http(
                port, "POST", "/api/goals",
                body={"goal_id": "process-goal", "repo": "o/repo", "number": 178},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            created = list(pool.map(register, (a, b)))
        assert [status for status, _ in created] == [200, 200]
        assert all(r["goal"]["version"] == 0 for _, r in created)
        assert db.launch(user_id="alice", goal_id="process-goal")["state"] == "PENDING"
        assert len([
            x for x in db.list_goals(user_id="alice")
            if x["goal_id"] == "process-goal"
        ]) == 1

        barrier = Barrier(2)

        def pause(port):
            barrier.wait(timeout=10)
            return _http(
                port, "POST", "/api/goals/process-goal/pause",
                body={"expected_version": 0},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(pause, (a, b)))
        assert sorted(status for status, _ in results) == [202, 409]
        assert db.goal(user_id="alice", goal_id="process-goal")["state"] == "PAUSED"
        assert db.goal(user_id="alice", goal_id="process-goal")["version"] == 1
        assert len(db.controls(user_id="alice", goal_id="process-goal")) == 1
        assert _http(b, "GET", "/api/goals/process-goal", who="bob")[0] == 404

        first.terminate()
        _join_or_kill(first)
        assert _http(b, "GET", "/api/goals/process-goal")[1]["goal"]["state"] == "PAUSED"
        assert _http(
            b, "POST", "/api/goals/process-goal/resume",
            body={"expected_version": 1},
        )[0] == 202
    assert db.goal(user_id="alice", goal_id="process-goal")["version"] == 2


def test_competing_os_processes_one_temporal_start_owner(db):
    db.register(user_id="alice", goal_id="claim-race", repo="o/repo", number=178)
    gate, results = SPAWN.Barrier(6), SPAWN.Queue()
    children = [
        SPAWN.Process(
            target=_claim_launch,
            args=(DSN, "claim-race", "owner-" + str(i), gate, results, False),
        ) for i in range(6)
    ]
    for child in children:
        child.start()
    try:
        rows = [results.get(timeout=20) for _ in children]
        assert sum(1 for row in rows if row[1]) == 1
        assert {row[3] for row in rows} == {1}
        assert db.launch(user_id="alice", goal_id="claim-race")["state"] == "IN_FLIGHT"
        assert db.claim(
            user_id="alice", goal_id="claim-race", owner="late",
        ).claimed_now is False
    finally:
        for child in children:
            _join_or_kill(child)
        results.close()
        results.join_thread()


def test_kill_during_launch_admission_becomes_unknown_not_duplicate_start(db):
    db.register(user_id="alice", goal_id="crash-start", repo="o/repo", number=178)
    gate, results = SPAWN.Barrier(1), SPAWN.Queue()
    child = SPAWN.Process(
        target=_claim_launch,
        args=(DSN, "crash-start", "doomed", gate, results, True),
    )
    child.start()
    try:
        owner, won, state, generation = results.get(timeout=20)
        assert (owner, won, state, generation) == ("doomed", True, "IN_FLIGHT", 1)
        child.terminate()
        _join_or_kill(child)
        time.sleep(1.3)
        assert db.recover_expired() >= 1
        assert db.launch(user_id="alice", goal_id="crash-start")["state"] == "UNKNOWN"
        assert db.claim(
            user_id="alice", goal_id="crash-start", owner="replacement",
        ).claimed_now is False
        assert db.launch(user_id="alice", goal_id="crash-start")["generation"] == 1
    finally:
        _join_or_kill(child)
        results.close()
        results.join_thread()


def test_competing_os_processes_one_notification_and_kill_unknown(db):
    db.register(user_id="alice", goal_id="notify-crash", repo="o/repo", number=178)
    db.observe(
        db.goal(user_id="alice", goal_id="notify-crash"),
        PullSnapshot(head_sha="a" * 40, state="OPEN", ci="FAILURE"),
        now=datetime.now(timezone.utc),
    )
    gate, results = SPAWN.Barrier(2), SPAWN.Queue()
    children = [
        SPAWN.Process(
            target=_claim_notification,
            args=(DSN, "notify-crash", "sender-" + str(i), gate, results, True),
        ) for i in range(2)
    ]
    for child in children:
        child.start()
    try:
        rows = [results.get(timeout=20) for _ in children]
        assert sum(item[1] for item in rows) == 1
        assert len({row[2] for row in rows if row[2] is not None}) == 1
        for child in children:
            if child.is_alive():
                child.terminate()
        for child in children:
            _join_or_kill(child)
        time.sleep(1.3)
        assert db.recover_expired_deliveries(user_id="alice") == 1
        assert db.delivery_counts(user_id="alice") == {"UNKNOWN": 1}
        assert db.claim_due(user_id="alice", owner="restart-sender") == []
    finally:
        for child in children:
            _join_or_kill(child)
        results.close()
        results.join_thread()


def test_killed_uncommitted_pg_session_rolls_back_goal_and_launch(db):
    ready = SPAWN.Queue()
    child = SPAWN.Process(target=_uncommitted_insert, args=(DSN, ready))
    child.start()
    try:
        assert ready.get(timeout=20) is True
        child.terminate()
        _join_or_kill(child)
        with pytest.raises(Exception, match="not found"):
            db.goal(user_id="alice", goal_id="killed-transaction")
        db.register(
            user_id="alice", goal_id="killed-transaction",
            repo="o/repo", number=1,
        )
        assert db.launch(user_id="alice", goal_id="killed-transaction")["state"] == "PENDING"
    finally:
        _join_or_kill(child)
        ready.close()
        ready.join_thread()
