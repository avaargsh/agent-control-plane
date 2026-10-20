"""Real PostgreSQL 16 V3 prepared-control concurrency and crash admission.

This test does not replace a database with fakes. Every test uses a separate
schema in an EXPLICIT, DISPOSABLE PR_WATCH_PG_TEST_DSN. SIGKILL children are
test-owned Python processes, never the PostgreSQL service or an external
process. No Temporal or remote provider is invoked in these tests.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import conninfo, sql  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from pr_watch_pg_admission import AdmissionError  # noqa: E402
from pr_watch_pg_migrations import PostgresMigrations  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")
SPAWN = multiprocessing.get_context("spawn")
USER = "alice"


@pytest.fixture
def isolated():
    if not DSN:
        pytest.skip("requires exclusive, disposable PR_WATCH_PG_TEST_DSN")
    name = "test_v3_chaos_" + uuid4().hex[:16]
    with psycopg.connect(DSN) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    scoped = conninfo.make_conninfo(DSN, options=f"-c search_path={name}")
    try:
        PostgresMigrations(scoped).upgrade(3)
        yield scoped
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def _store(dsn, goal_id="race"):
    store = PostgresWatchStore(dsn)
    store.register(user_id=USER, goal_id=goal_id, repo="o/repo", number=169)
    return store


def _command(goal_id, request_id, action="pause", version=0, user=USER):
    return {
        "user_id": user, "goal_id": goal_id, "request_id": request_id,
        "action": action, "expected_version": version,
    }


def test_parallel_same_id_commits_one_cas_and_returns_only_replays(isolated):
    store = _store(isolated)
    command = _command("race", "1" * 32)
    assert store.prepare_control(**command)["state"] == "PREPARED"
    workers = 6
    gate = Barrier(workers)

    def invoke(_):
        gate.wait(timeout=15)
        return PostgresWatchStore(isolated).transition_with_request(**command)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(invoke, range(workers)))
    assert sum(not replayed for _, replayed in results) == 1
    assert sum(replayed for _, replayed in results) == workers - 1
    assert all(goal["version"] == 1 for goal, _ in results)
    assert store.goal(user_id=USER, goal_id="race")["state"] == "PAUSED"
    assert store.control_request(
        user_id=USER, request_id=command["request_id"],
    )["committed_version"] == 1
    assert [row["version"] for row in store.controls(
        user_id=USER, goal_id="race",
    )] == [1]


def test_different_keys_compete_for_one_goal_version_without_second_commit(isolated):
    store = _store(isolated)
    commands = [
        _command("race", "2" * 32),
        _command("race", "3" * 32),
    ]
    for command in commands:
        assert store.prepare_control(**command)["state"] == "PREPARED"
    gate = Barrier(2)

    def invoke(command):
        gate.wait(timeout=15)
        try:
            _, replayed = PostgresWatchStore(isolated).transition_with_request(
                **command,
            )
            return ("COMMITTED", replayed)
        except AdmissionError as error:
            return ("REJECTED", str(error))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, commands))
    assert sum(row[0] == "COMMITTED" for row in results) == 1
    assert sum(row[0] == "REJECTED" for row in results) == 1
    assert store.goal(user_id=USER, goal_id="race")["version"] == 1
    assert len(store.controls(user_id=USER, goal_id="race")) == 1
    states = {
        store.control_request(
            user_id=USER, request_id=command["request_id"],
        )["state"] for command in commands
    }
    assert states == {"COMMITTED", "PREPARED"}
    # The losing request does not silently retry against the new version.
    loser = next(command for command in commands if store.control_request(
        user_id=USER, request_id=command["request_id"],
    )["state"] == "PREPARED")
    with pytest.raises(AdmissionError, match="stale version"):
        store.transition_with_request(**loser)
    assert len(store.controls(user_id=USER, goal_id="race")) == 1


def test_same_random_id_remains_owner_scoped_and_cannot_rebind_goal(isolated):
    store = _store(isolated)
    store.register(user_id="bob", goal_id="race", repo="o/repo", number=169)
    store.register(user_id=USER, goal_id="other", repo="o/repo", number=170)
    request_id = "a" * 32
    alice = _command("race", request_id)
    bob = _command("race", request_id, user="bob")
    assert store.prepare_control(**alice)["state"] == "PREPARED"
    assert store.prepare_control(**bob)["state"] == "PREPARED"
    with pytest.raises(AdmissionError, match="already bound"):
        store.prepare_control(**{**alice, "goal_id": "other"})
    with pytest.raises(AdmissionError, match="already bound"):
        store.transition_with_request(**{**alice, "action": "cancel"})
    assert store.transition_with_request(**alice)[1] is False
    assert store.control_request(
        user_id="bob", request_id=request_id,
    )["state"] == "PREPARED"
    assert store.transition_with_request(**bob)[1] is False
    assert store.goal(user_id="bob", goal_id="race")["version"] == 1
    assert store.list_control_requests(user_id="alice")[0]["user_id"] == "alice"
    assert store.list_control_requests(user_id="bob")[0]["user_id"] == "bob"


def _crash_child(dsn, command, pipe, phase):
    """Run the real production transition and pause at the transaction boundary."""
    try:
        store = PostgresWatchStore(dsn)
        if phase == "before_commit":
            @contextmanager
            def blocked_transaction():
                with psycopg.connect(
                    dsn, row_factory=dict_row, connect_timeout=5,
                ) as conn:
                    yield conn
                    # This is after the real store's 3 SQL writes, BEFORE
                    # psycopg connection __exit__ commits the transaction.
                    pipe.send("MUTATED_NOT_COMMITTED")
                    time.sleep(30)

            store._connection = blocked_transaction
        _, replayed = store.transition_with_request(**command)
        if phase == "after_commit":
            pipe.send(("COMMITTED_NO_ACK", replayed))
            time.sleep(30)
    except Exception as error:
        pipe.send(("ERROR", type(error).__name__, str(error)))
    finally:
        pipe.close()


def _kill_at_phase(dsn, command, phase):
    parent, child_pipe = SPAWN.Pipe(duplex=False)
    child = SPAWN.Process(target=_crash_child, args=(
        dsn, command, child_pipe, phase,
    ))
    child.start()
    child_pipe.close()
    try:
        assert parent.poll(25), "child did not reach the expected crash boundary"
        message = parent.recv()
        assert message == ("MUTATED_NOT_COMMITTED" if phase == "before_commit"
                           else ("COMMITTED_NO_ACK", False)), message
        assert child.is_alive()
        child.kill()  # SIGKILL prevents normal context manager commit.
        child.join(timeout=10)
        assert not child.is_alive()
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=10)
        parent.close()


def test_sigkill_before_sql_transaction_commit_rolls_back_all_three_writes(isolated):
    store = _store(isolated)
    command = _command("race", "b" * 32)
    store.prepare_control(**command)
    _kill_at_phase(isolated, command, "before_commit")

    reopened = PostgresWatchStore(isolated)
    assert reopened.goal(user_id=USER, goal_id="race")["version"] == 0
    assert reopened.controls(user_id=USER, goal_id="race") == []
    record = reopened.control_request(
        user_id=USER, request_id=command["request_id"],
    )
    assert record["state"] == "PREPARED"
    assert record["committed_version"] is None
    goal, replayed = reopened.transition_with_request(**command)
    assert goal["version"] == 1 and replayed is False
    assert reopened.transition_with_request(**command)[1] is True
    assert len(reopened.controls(user_id=USER, goal_id="race")) == 1


def test_sigkill_after_commit_before_ack_replays_receipt_without_new_writes(isolated):
    store = _store(isolated)
    command = _command("race", "c" * 32)
    store.prepare_control(**command)
    _kill_at_phase(isolated, command, "after_commit")

    reopened = PostgresWatchStore(isolated)
    assert reopened.goal(user_id=USER, goal_id="race")["version"] == 1
    assert reopened.control_request(
        user_id=USER, request_id=command["request_id"],
    )["state"] == "COMMITTED"
    assert reopened.controls(user_id=USER, goal_id="race")[0]["action"] == "pause"
    goal, replayed = reopened.transition_with_request(**command)
    assert replayed is True and goal["version"] == 1
    assert len(reopened.controls(user_id=USER, goal_id="race")) == 1


def test_pg_send_claim_has_one_owner_across_parallel_api_senders(isolated):
    store = _store(isolated)
    command = _command("race", "d" * 32)
    store.prepare_control(**command)
    goal, replayed = store.transition_with_request(**command)
    assert not replayed and goal["version"] == 1
    assert store.control(user_id=USER, goal_id="race", version=1)[
        "evidence"
    ] == "UNATTEMPTED_V3"
    gate = Barrier(8)

    def claim(_):
        gate.wait(timeout=15)
        return PostgresWatchStore(isolated).claim_control_signal(
            user_id=USER, goal_id="race", version=1, unattempted_only=True,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(claim, range(8)))
    assert sum(record is not None for record in attempts) == 1
    assert store.control(user_id=USER, goal_id="race", version=1)[
        "signal_state"
    ] == "UNKNOWN"
    # Restart with a new DB connection: no claim can send the Signal again.
    assert PostgresWatchStore(isolated).claim_control_signal(
        user_id=USER, goal_id="race", version=1, unattempted_only=True,
    ) is None
    with pytest.raises(AdmissionError, match="not found"):
        store.claim_control_signal(
            user_id="bob", goal_id="race", version=1,
            unattempted_only=True,
        )


def test_pg_legacy_pending_cannot_be_claimed_by_explicit_recovery(isolated):
    store = _store(isolated)
    store.transition(
        user_id=USER, goal_id="race", action="pause", expected_version=0,
    )
    assert store.control(user_id=USER, goal_id="race", version=1)[
        "evidence"
    ] is None
    assert store.claim_control_signal(
        user_id=USER, goal_id="race", version=1, unattempted_only=True,
    ) is None
    assert store.control(user_id=USER, goal_id="race", version=1)[
        "signal_state"
    ] == "PENDING"


def _claim_then_die(dsn, ready):
    store = PostgresWatchStore(dsn)
    claimed = store.claim_control_signal(
        user_id=USER, goal_id="race", version=1,
        unattempted_only=True,
    )
    ready.send(claimed is not None)
    ready.close()
    time.sleep(30)


def test_pg_sigkill_after_signal_claim_cannot_dispatch_again(isolated):
    store = _store(isolated)
    command = _command("race", "e" * 32)
    store.prepare_control(**command)
    store.transition_with_request(**command)
    recv, send = SPAWN.Pipe(duplex=False)
    proc = SPAWN.Process(target=_claim_then_die, args=(isolated, send))
    proc.start()
    send.close()
    try:
        assert recv.poll(15), "child never committed the send claim"
        assert recv.recv() is True
        assert proc.is_alive()
        proc.kill()
        proc.join(timeout=10)
        assert not proc.is_alive()
        restarted = PostgresWatchStore(isolated)
        assert restarted.control(user_id=USER, goal_id="race", version=1)[
            "signal_state"
        ] == "UNKNOWN"
        assert restarted.claim_control_signal(
            user_id=USER, goal_id="race", version=1,
            unattempted_only=True,
        ) is None
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=10)
        recv.close()
