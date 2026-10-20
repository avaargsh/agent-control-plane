"""End-to-end V3 HTTP idempotency through two real pilot API OS processes.

Opt-in, disposable PostgreSQL 16 only. Uses isolated random schema; no remote
Temporal endpoint, live GitHub, or production credentials. Real TCP HTTP,
PG row/advisory locks, and process boundaries are exercised.
"""
from __future__ import annotations

import http.client
import json
import multiprocessing
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import conninfo, sql  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from pr_watch_pg_migrations import PostgresMigrations  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")
SPAWN = multiprocessing.get_context("spawn")
TOKENS = {"alice": "a" * 40, "bob": "b" * 40}


@pytest.fixture
def isolated():
    if not DSN:
        pytest.skip("requires exclusive, disposable PR_WATCH_PG_TEST_DSN")
    schema = "test_v3_http_" + uuid4().hex[:16]
    with psycopg.connect(DSN) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = conninfo.make_conninfo(DSN, options=f"-c search_path={schema}")
    try:
        PostgresMigrations(scoped).upgrade(3)
        yield scoped
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _serve(dsn, ready, stop):
    from threading import Thread

    from pr_watch_service import LocalTokens, PilotHTTPServer, PilotService

    server = PilotHTTPServer(
        ("127.0.0.1", 0),
        PilotService(
            None, LocalTokens(TOKENS),
            storage_backend="postgres", pg_dsn=dsn,
        ),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ready.send(server.server_port)
    ready.close()
    try:
        stop.wait(35)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@contextmanager
def _api_process(dsn):
    read, write = SPAWN.Pipe(duplex=False)
    stop = SPAWN.Event()
    process = SPAWN.Process(target=_serve, args=(dsn, write, stop))
    process.start()
    write.close()
    try:
        assert read.poll(20), "API child did not bind its own HTTP listener"
        port = read.recv()
        assert process.is_alive()
        yield process, port
    finally:
        # Never set a dead child's synchronization Event after forced SIGKILL.
        if process.is_alive():
            stop.set()
        process.join(timeout=8)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        assert not process.is_alive()
        read.close()


def _http(port, method, path, *, who="alice", body=None):
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Authorization": "Bearer " + TOKENS[who]}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        client.request(method, path, body=payload, headers=headers)
        response = client.getresponse()
        return response.status, json.loads(response.read())
    finally:
        client.close()


def test_two_http_processes_share_one_prepared_request_and_one_cas(isolated):
    store = PostgresWatchStore(isolated)
    store.register(user_id="alice", goal_id="pr-shared", repo="o/repo", number=169)
    request_id = "1" * 32
    prep = {
        "request_id": request_id, "action": "pause", "expected_version": 0,
    }
    with _api_process(isolated) as (first, a), _api_process(isolated) as (_, b):
        status, reply = _http(
            a, "POST", "/api/goals/pr-shared/controls/prepare", body=prep,
        )
        assert status == 200 and reply["request"]["state"] == "PREPARED"
        status, reply = _http(
            b, "POST", "/api/goals/pr-shared/controls/prepare", body=prep,
        )
        assert status == 200 and reply["request"]["request_id"] == request_id

        versions = {"expected_version": 0, "request_id": request_id}
        gate = Barrier(2)

        def send(port):
            gate.wait(timeout=12)
            return _http(port, "POST", "/api/goals/pr-shared/pause", body=versions)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send, (a, b)))
        assert sorted(status for status, _ in responses) == [202, 202]
        assert sorted(data["replayed"] for _, data in responses) == [False, True]
        assert {data["control"]["receipt_id"] for _, data in responses} == {
            responses[0][1]["control"]["receipt_id"],
        }
        assert store.goal(user_id="alice", goal_id="pr-shared")["version"] == 1
        assert len(store.controls(user_id="alice", goal_id="pr-shared")) == 1
        assert store.control_request(
            user_id="alice", request_id=request_id,
        )["state"] == "COMMITTED"
        assert _http(
            b, "GET", "/api/control-requests/" + request_id, who="bob",
        )[0] == 404
        assert _http(b, "GET", "/api/control-requests", who="bob")[1][
            "requests"
        ] == []
        assert _http(
            a, "POST", "/api/goals/pr-shared/controls/prepare",
            body={**prep, "action": "cancel"},
        )[0] == 409

        # Deliberately kill one API after the durable commit. The surviving
        # process still serves the same receipt without another Goal CAS.
        first.kill()
        first.join(timeout=8)
        assert not first.is_alive()
        status, data = _http(
            b, "POST", "/api/goals/pr-shared/pause", body=versions,
        )
        assert status == 202 and data["replayed"] is True
        assert len(store.controls(user_id="alice", goal_id="pr-shared")) == 1


def test_distinct_http_request_ids_race_one_expected_version(isolated):
    store = PostgresWatchStore(isolated)
    store.register(user_id="alice", goal_id="pr-competition", repo="o/repo", number=170)
    with _api_process(isolated) as (_, a), _api_process(isolated) as (_, b):
        for port, request_id in ((a, "2" * 32), (b, "3" * 32)):
            status, record = _http(
                port, "POST", "/api/goals/pr-competition/controls/prepare",
                body={
                    "request_id": request_id, "action": "pause",
                    "expected_version": 0,
                },
            )
            assert status == 200 and record["request"]["state"] == "PREPARED"
        gate = Barrier(2)

        def post(port, request_id):
            gate.wait(timeout=12)
            return _http(
                port, "POST", "/api/goals/pr-competition/pause",
                body={"expected_version": 0, "request_id": request_id},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(
                lambda args: post(*args), ((a, "2" * 32), (b, "3" * 32)),
            ))
        assert sorted(status for status, _ in responses) == [202, 409]
        assert len(store.controls(user_id="alice", goal_id="pr-competition")) == 1
        assert sorted(
            record["state"] for record in store.list_control_requests(user_id="alice")
        ) == ["COMMITTED", "PREPARED"]
        loser = "2" * 32 if responses[0][0] == 409 else "3" * 32
        assert _http(
            b, "GET", "/api/control-requests/" + loser,
        )[1]["request"]["state"] == "PREPARED"
        status, _ = _http(
            b, "POST", "/api/goals/pr-competition/pause",
            body={"expected_version": 0, "request_id": loser},
        )
        assert status == 409
        assert len(store.controls(user_id="alice", goal_id="pr-competition")) == 1
