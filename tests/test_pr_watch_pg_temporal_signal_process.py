"""Real Temporal Signal history and PostgreSQL V3 across independent API PIDs.

Runs only with an EXCLUSIVE, DISPOSABLE PR_WATCH_PG_TEST_DSN. Starts a real
Temporal SDK test server (not a mock), two distinct HTTP API subprocesses and
a temporary PostgreSQL schema. No external GitHub/Telegram/production RPC.
Only test-owned API subprocesses are killed.
"""
from __future__ import annotations

import asyncio
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
pytest.importorskip("temporalio")
from psycopg import conninfo, sql  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from pr_watch_control_bridge import control_signal_id, workflow_id  # noqa: E402
from pr_watch_pg_migrations import PostgresMigrations  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402
from pr_watch_temporal import PRWatchWorkflow  # noqa: E402

DSN = os.environ.get("PR_WATCH_PG_TEST_DSN")
SPAWN = multiprocessing.get_context("spawn")
TOKENS = {"alice": "a" * 40, "bob": "b" * 40}
USER = "alice"


@pytest.fixture
def isolated():
    if not DSN:
        pytest.skip("exclusive, disposable PR_WATCH_PG_TEST_DSN is required")
    schema = "test_signal_e2e_" + uuid4().hex[:16]
    with psycopg.connect(DSN) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = conninfo.make_conninfo(DSN, options=f"-c search_path={schema}")
    try:
        PostgresMigrations(scoped).upgrade(3)
        yield scoped
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _serve(dsn, address, mode, ready, marker, stop):
    """The real HTTP handler, production bridge and own PG/Temporal connections."""
    from threading import Thread
    from pr_watch_control_bridge import TemporalControlBridge
    from pr_watch_service import LocalTokens, PilotHTTPServer, PilotService

    class PausingBridge(TemporalControlBridge):
        async def _signal(self, *, user_id, goal_id, version, action):
            if mode == "crash-before-rpc":
                marker.send("CLAIMED_BUT_NOT_SENT")
                # Deliberately die before reaching the real Temporal SDK.
                await asyncio.Event().wait()
            result = await super()._signal(
                user_id=user_id, goal_id=goal_id,
                version=version, action=action,
            )
            if mode == "crash-after-rpc":
                marker.send("TEMPORAL_ACCEPTED_BUT_NOT_RECORDED")
                # History is durable, but local journal is still UNKNOWN.
                await asyncio.Event().wait()
            return result

    bridge = PausingBridge(address=address)
    server = PilotHTTPServer(
        ("127.0.0.1", 0),
        PilotService(
            None, LocalTokens(TOKENS), bridge=bridge,
            storage_backend="postgres", pg_dsn=dsn,
        ),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ready.send(server.server_port)
    ready.close()
    try:
        stop.wait(90)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
        marker.close()


@contextmanager
def _api_process(dsn, address, mode="normal"):
    ready_read, ready_write = SPAWN.Pipe(duplex=False)
    marker_read, marker_write = SPAWN.Pipe(duplex=False)
    stop = SPAWN.Event()
    child = SPAWN.Process(
        target=_serve, args=(dsn, address, mode, ready_write, marker_write, stop),
    )
    child.start()
    ready_write.close()
    marker_write.close()
    try:
        assert ready_read.poll(20), "test API child did not open HTTP listener"
        port = ready_read.recv()
        assert child.is_alive()
        yield child, port, marker_read
    finally:
        # Setting multiprocessing.Event on a killed process can deadlock;
        # only signal children that are still alive.
        if child.is_alive():
            stop.set()
        child.join(timeout=8)
        if child.is_alive():
            child.kill()
            child.join(timeout=8)
        assert not child.is_alive()
        ready_read.close()
        marker_read.close()


def _http(port, method, path, *, who=USER, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Authorization": "Bearer " + TOKENS[who]}
    data = None if body is None else json.dumps(body).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def _committed(dsn, goal_id, request_id):
    store = PostgresWatchStore(dsn)
    store.register(user_id=USER, goal_id=goal_id, repo="o/repo", number=169)
    command = dict(
        user_id=USER, goal_id=goal_id, request_id=request_id,
        action="pause", expected_version=0,
    )
    assert store.prepare_control(**command)["state"] == "PREPARED"
    goal, replayed = store.transition_with_request(**command)
    assert not replayed and goal["version"] == 1
    assert store.control(user_id=USER, goal_id=goal_id, version=1)[
        "evidence"
    ] == "UNATTEMPTED_V3"
    return store


async def _start(env, goal_id):
    return await env.client.start_workflow(
        PRWatchWorkflow.run,
        {"user_id": USER, "goal_id": goal_id, "interval_seconds": 86400},
        id=workflow_id(user_id=USER, goal_id=goal_id),
        task_queue="no-worker-necessary-history-admission",
    )


async def _accepted_signals(env, handle, signal_id):
    history = await handle.fetch_history()
    matched = 0
    for event in history.events:
        attrs = event.workflow_execution_signaled_event_attributes
        if attrs.signal_name != "wake" or not attrs.input.payloads:
            continue
        args = await env.client.data_converter.decode(attrs.input.payloads)
        if args == [signal_id]:
            matched += 1
    return matched


def _dispatch(port, goal_id):
    return _http(
        port, "POST",
        "/api/goals/" + goal_id + "/controls/1/dispatch-unattempted",
        body={},
    )


def _abort_after_marker(child, marker, expected):
    assert marker.poll(20), "test-only bridge did not reach crash boundary"
    assert marker.recv() == expected
    assert child.is_alive()
    child.kill()
    child.join(timeout=8)
    assert not child.is_alive()


def test_two_real_api_processes_one_temporal_signal_history_event(isolated):
    goal_id = "two-senders"
    store = _committed(isolated, goal_id, "1" * 32)
    identifier = control_signal_id(
        user_id=USER, goal_id=goal_id, version=1, action="pause",
    )

    async def scenario():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            handle = await _start(env, goal_id)
            address = env.client.service_client.config.target_host
            with _api_process(isolated, address) as (_, a, _), \
                 _api_process(isolated, address) as (_, b, _):
                gate = Barrier(2)

                def send(port):
                    gate.wait(timeout=12)
                    return _dispatch(port, goal_id)

                with ThreadPoolExecutor(max_workers=2) as pool:
                    replies = await asyncio.to_thread(
                        lambda: list(pool.map(send, (a, b))),
                    )
                assert all(status in (200, 202, 409) for status, _ in replies)
                assert any(status == 200 for status, _ in replies)
                assert store.control(
                    user_id=USER, goal_id=goal_id, version=1,
                )["signal_state"] == "ACKED"
                assert await _accepted_signals(env, handle, identifier) == 1
                assert _http(
                    b, "GET", "/api/goals/" + goal_id + "/controls/1",
                    who="bob",
                )[0] == 404
                assert _dispatch(b, goal_id)[0] == 409
                status, reply = _http(
                    b, "POST", "/api/goals/" + goal_id + "/pause",
                    body={"expected_version": 0, "request_id": "1" * 32},
                )
                assert status == 200 and reply["replayed"] is True
                assert await _accepted_signals(env, handle, identifier) == 1
                assert len(store.controls(user_id=USER, goal_id=goal_id)) == 1

    asyncio.run(scenario())


def test_sigkill_after_claim_before_temporal_rpc_remains_unknown(isolated):
    goal_id = "claim-then-killed"
    store = _committed(isolated, goal_id, "2" * 32)
    identifier = control_signal_id(
        user_id=USER, goal_id=goal_id, version=1, action="pause",
    )

    async def scenario():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            handle = await _start(env, goal_id)
            address = env.client.service_client.config.target_host
            with _api_process(isolated, address, "crash-before-rpc") as (child, port, marker), \
                 _api_process(isolated, address) as (_, survivor, _):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    request = pool.submit(_dispatch, port, goal_id)
                    await asyncio.to_thread(
                        _abort_after_marker, child, marker, "CLAIMED_BUT_NOT_SENT",
                    )
                    with pytest.raises((OSError, http.client.HTTPException)):
                        await asyncio.wrap_future(request)
                record = store.control(user_id=USER, goal_id=goal_id, version=1)
                assert record["signal_state"] == "UNKNOWN"
                assert record["evidence"] == "ACK_UNCERTAIN"
                assert await _accepted_signals(env, handle, identifier) == 0
                assert _dispatch(survivor, goal_id)[0] == 409
                status, reply = _http(
                    survivor, "POST",
                    "/api/goals/" + goal_id + "/controls/1/reconcile",
                    body={},
                )
                assert status == 200 and reply["control"]["signal_state"] == "UNKNOWN"
                assert await _accepted_signals(env, handle, identifier) == 0
                assert len(store.controls(user_id=USER, goal_id=goal_id)) == 1

    asyncio.run(scenario())


def test_sigkill_after_temporal_rpc_before_ack_reconciles_history(isolated):
    goal_id = "signal-ack-killed"
    store = _committed(isolated, goal_id, "3" * 32)
    identifier = control_signal_id(
        user_id=USER, goal_id=goal_id, version=1, action="pause",
    )

    async def scenario():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            handle = await _start(env, goal_id)
            address = env.client.service_client.config.target_host
            with _api_process(isolated, address, "crash-after-rpc") as (child, port, marker), \
                 _api_process(isolated, address) as (_, survivor, _):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    request = pool.submit(_dispatch, port, goal_id)
                    await asyncio.to_thread(
                        _abort_after_marker, child, marker,
                        "TEMPORAL_ACCEPTED_BUT_NOT_RECORDED",
                    )
                    with pytest.raises((OSError, http.client.HTTPException)):
                        await asyncio.wrap_future(request)
                assert store.control(
                    user_id=USER, goal_id=goal_id, version=1,
                )["signal_state"] == "UNKNOWN"
                assert await _accepted_signals(env, handle, identifier) == 1
                assert _dispatch(survivor, goal_id)[0] == 409
                status, reply = _http(
                    survivor, "POST",
                    "/api/goals/" + goal_id + "/controls/1/reconcile",
                    body={},
                )
                assert status == 200
                assert reply["control"]["signal_state"] == "ACKED"
                assert reply["control"]["evidence"] == "HISTORY_PROOF"
                assert await _accepted_signals(env, handle, identifier) == 1
                status, reply = _http(
                    survivor, "POST", "/api/goals/" + goal_id + "/pause",
                    body={"expected_version": 0, "request_id": "3" * 32},
                )
                assert status == 200 and reply["replayed"] is True
                assert await _accepted_signals(env, handle, identifier) == 1
                assert len(store.controls(user_id=USER, goal_id=goal_id)) == 1

    asyncio.run(scenario())
