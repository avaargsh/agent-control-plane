"""Real PostgreSQL admission and Temporal time-skipping integration tests.

Use a disposable PostgreSQL DB. Never run against a database with user data:
the session fixture truncates the pilot's two tables.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest

psycopg = pytest.importorskip("psycopg")
temporalio = pytest.importorskip("temporalio")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from temporalio.testing import WorkflowEnvironment

from pr_watch_control_bridge import workflow_id
from pr_watch_launcher import TemporalLaunchBridge
from pr_watch_pg_admission import AdmissionError, PostgresAdmission
from pr_watch_pg_dispatcher import PostgresLaunchDispatcher
from pr_watch_temporal import TASK_QUEUE

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")


@pytest.fixture(scope="session")
def pg():
    if not DSN:
        pytest.skip("requires disposable PR_WATCH_PG_TEST_DSN")
    store = PostgresAdmission(DSN)
    store.install_v1()
    with psycopg.connect(DSN) as conn:
        conn.execute("TRUNCATE pr_watch_pg_launches,pr_watch_pg_goals")
    yield store


@pytest.fixture
def store(pg, request):
    # All tests operate on isolated IDs without relying on ordering.
    user = "user-" + request.node.name.replace("_", "-")[:65]
    return pg, user


def register(pg, user, goal="pr-169"):
    return pg.register(
        user_id=user, goal_id=goal,
        repo="avaargsh/agent-control-plane", number=169,
    )


def test_registration_is_atomic_idempotent_and_user_scoped(store):
    pg, user = store
    first = register(pg, user)
    assert first["state"] == "RUNNING" and first["version"] == 0
    assert pg.launch(user_id=user, goal_id="pr-169")["state"] == "PENDING"
    assert register(pg, user) == first
    with pytest.raises(AdmissionError, match="rebound"):
        pg.register(user_id=user, goal_id="pr-169", repo="other/repo", number=10)
    with pytest.raises(AdmissionError, match="not found"):
        pg.goal(user_id="other-user", goal_id="pr-169")
    with pytest.raises(AdmissionError, match="not found"):
        pg.launch(user_id="other-user", goal_id="pr-169")
    assert pg.launch(user_id=user, goal_id="pr-169")["state"] == "PENDING"


def test_multi_connection_registration_and_claim_have_one_winner(store):
    pg, user = store
    gate = Barrier(12)
    def work(i):
        register(pg, user)
        gate.wait(timeout=15)
        return pg.claim(
            user_id=user, goal_id="pr-169",
            owner="worker-" + str(i),
        )
    with ThreadPoolExecutor(max_workers=12) as pool:
        claims = list(pool.map(work, range(12)))
    winners = [claim for claim in claims if claim.claimed_now]
    assert len(winners) == 1
    assert all(c.state == "IN_FLIGHT" for c in claims)
    assert winners[0].generation == 1
    ack = pg.finalize(claim=winners[0], state="ACKED")
    assert ack["state"] == "ACKED"
    assert pg.claim(
        user_id=user, goal_id="pr-169", owner="future-worker",
    ).claimed_now is False
    assert pg.launch(user_id=user, goal_id="pr-169")["generation"] == 1


def test_owner_generation_fences_stale_claim_and_cross_user(store):
    pg, user = store
    register(pg, user)
    claimed = pg.claim(user_id=user, goal_id="pr-169", owner="winner")
    assert claimed.claimed_now is True
    with pytest.raises(AdmissionError, match="fenced"):
        pg.finalize(
            claim=replace(claimed, owner="attacker"),
            state="ACKED",
        )
    with pytest.raises(AdmissionError, match="fenced"):
        pg.finalize(
            claim=replace(claimed, generation=100),
            state="ACKED",
        )
    assert pg.launch(user_id=user, goal_id="pr-169")["state"] == "IN_FLIGHT"
    assert pg.finalize(claim=claimed, state="ACKED")["state"] == "ACKED"
    with pytest.raises(AdmissionError, match="fenced"):
        pg.finalize(claim=claimed, state="ACKED")


def test_expired_claim_goes_unknown_not_back_to_pending(store):
    pg, user = store
    register(pg, user)
    claim = pg.claim(
        user_id=user, goal_id="pr-169",
        owner="crashed", timeout_seconds=1,
    )
    time.sleep(1.25)
    assert pg.recover_expired() >= 1
    assert pg.launch(user_id=user, goal_id="pr-169")["state"] == "UNKNOWN"
    with pytest.raises(AdmissionError, match="fenced"):
        pg.finalize(claim=claim, state="ACKED")
    assert not pg.claim(
        user_id=user, goal_id="pr-169", owner="restart",
    ).claimed_now
    assert pg.launch(user_id=user, goal_id="pr-169")["state"] == "UNKNOWN"
    assert pg.pending(limit=100)


def test_active_claim_never_recovered_and_late_positive_proof_allowed(store):
    pg, user = store
    register(pg, user)
    c = pg.claim(
        user_id=user, goal_id="pr-169", owner="active", timeout_seconds=120,
    )
    before = pg.launch(user_id=user, goal_id="pr-169")
    pg.recover_expired()
    after = pg.launch(user_id=user, goal_id="pr-169")
    assert after == before
    assert c.claimed_now
    assert pg.observe_existing(
        user_id=user, goal_id="pr-169", workflow_status="RUNNING",
    )["state"] == "OBSERVED"
    with pytest.raises(AdmissionError, match="fenced"):
        pg.finalize(claim=c, state="UNKNOWN")
    assert pg.launch(user_id=user, goal_id="pr-169")["evidence"] == "DESCRIBE_EXISTS"


def test_goal_state_controls_admission_and_never_reactivates_cancelled(store):
    pg, user = store
    register(pg, user, "paused")
    pg.set_goal_state(
        user_id=user, goal_id="paused",
        state="PAUSED", expected_version=0,
    )
    assert pg.claim(
        user_id=user, goal_id="paused", owner="worker",
    ).claimed_now
    register(pg, user, "cancelled")
    pg.set_goal_state(
        user_id=user, goal_id="cancelled",
        state="CANCELLED", expected_version=0,
    )
    c = pg.claim(user_id=user, goal_id="cancelled", owner="worker")
    assert c.state == "SKIPPED" and not c.claimed_now
    assert pg.launch(user_id=user, goal_id="cancelled")["evidence"] == "GOAL_INACTIVE"
    with pytest.raises(AdmissionError, match="stale|invalid"):
        pg.set_goal_state(
            user_id=user, goal_id="cancelled",
            state="RUNNING", expected_version=1,
        )
    assert pg.launch(user_id=user, goal_id="cancelled")["state"] == "SKIPPED"


def test_cas_allows_only_one_state_transition(store):
    pg, user = store
    register(pg, user)
    def pause(_):
        try:
            pg.set_goal_state(
                user_id=user, goal_id="pr-169",
                state="PAUSED", expected_version=0,
            )
            return True
        except AdmissionError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(pause, range(8)))
    assert sum(result) == 1
    assert pg.goal(user_id=user, goal_id="pr-169")["version"] == 1


def test_unknown_not_promoted_by_missing_evidence(store):
    pg, user = store
    register(pg, user)
    claim = pg.claim(user_id=user, goal_id="pr-169", owner="worker")
    pg.finalize(claim=claim, state="UNKNOWN")
    with pytest.raises(AdmissionError, match="status"):
        pg.observe_existing(
            user_id=user, goal_id="pr-169", workflow_status="INVENTED",
        )
    assert pg.launch(user_id=user, goal_id="pr-169")["state"] == "UNKNOWN"
    assert pg.observe_existing(
        user_id=user, goal_id="pr-169", workflow_status="COMPLETED",
    )["state"] == "OBSERVED"
    assert pg.launch(user_id=user, goal_id="pr-169")["workflow_status"] == "COMPLETED"


def test_new_goal_registration_is_not_accepted_from_wrong_identity(store):
    pg, user = store
    for invalid in ("bad/path", "", "bad\nid", " "):
        with pytest.raises(AdmissionError):
            pg.register(
                user_id=user, goal_id=invalid, repo="o/r", number=1,
            )
    with pytest.raises(AdmissionError):
        pg.register(user_id=user, goal_id="bad", repo="o/r", number=True)
    with pytest.raises(AdmissionError):
        pg.claim(user_id=user, goal_id="absent", owner="worker")


def test_real_temporal_postgres_dispatch_and_reconcile(store):
    pg, user = store
    register(pg, user)
    async def scenario():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async def factory():
                return env.client
            temporal = TemporalLaunchBridge(
                address="test:7233", client_factory=factory,
            )
            dispatcher = PostgresLaunchDispatcher(store=pg, temporal=temporal)
            ack = await asyncio.to_thread(
                dispatcher.start_once,
                user_id=user, goal_id="pr-169", owner="api-1",
            )
            assert ack["state"] == "ACKED"
            assert ack["generation"] == 1
            assert ack["owner"] == "api-1"
            assert await asyncio.to_thread(
                dispatcher.start_once,
                user_id=user, goal_id="pr-169", owner="api-2",
            ) == ack
            observed = await asyncio.to_thread(
                dispatcher.reconcile, user_id=user, goal_id="pr-169",
            )
            assert observed["state"] == "OBSERVED"
            assert observed["workflow_status"] == "RUNNING"
            assert pg.launch(user_id=user, goal_id="pr-169")["evidence"] == "DESCRIBE_EXISTS"
    asyncio.run(scenario())


def test_real_temporal_existing_workflow_does_not_create_duplicate(store):
    pg, user = store
    register(pg, user)
    async def scenario():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async def factory():
                return env.client
            temporal = TemporalLaunchBridge(
                address="test:7233", client_factory=factory,
            )
            # Existing start accepted externally before this PG call.
            from pr_watch_temporal import PRWatchWorkflow
            handle = await env.client.start_workflow(
                PRWatchWorkflow.run,
                {"user_id": user, "goal_id": "pr-169", "interval_seconds": 300},
                id=workflow_id(user_id=user, goal_id="pr-169"),
                task_queue=TASK_QUEUE,
            )
            result = await asyncio.to_thread(
                PostgresLaunchDispatcher(store=pg, temporal=temporal).start_once,
                user_id=user, goal_id="pr-169", owner="api-restarted",
            )
            assert result["state"] == "OBSERVED"
            assert result["workflow_status"] == "RUNNING"
            assert handle.id == workflow_id(user_id=user, goal_id="pr-169")
    asyncio.run(scenario())
