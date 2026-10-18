"""Temporal enrollment CAS, crash ambiguity and actual SDK test-server acceptance."""
from __future__ import annotations

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("temporalio")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from temporalio.testing import WorkflowEnvironment

from pr_watch_control_bridge import workflow_id
from pr_watch_harness import GoalStore, WatchError
from pr_watch_launcher import TemporalLaunchBridge
from pr_watch_temporal import PRWatchWorkflow, TASK_QUEUE

USER = "alice"
GOAL = "pr-169"


def registered(tmp_path, *, goal_id=GOAL, user=USER):
    store = GoalStore(tmp_path / "goals.db")
    store.register(
        user_id=user, goal_id=goal_id,
        repo="avaargsh/agent-control-plane", number=169,
    )
    return store


class FakeHandle:
    def __init__(self, existing, workflow_id_value):
        self.existing = existing
        self.key = workflow_id_value

    async def describe(self):
        if self.key not in self.existing:
            raise OSError("Temporal Workflow missing or unreachable")
        return SimpleNamespace(status=SimpleNamespace(name=self.existing[self.key]))


class FakeClient:
    def __init__(self):
        self.starts = []
        self.existing = {}
        self.failure = False
        self.accept_then_ack_loss = False

    async def start_workflow(self, run, spec, **kwargs):
        assert run == PRWatchWorkflow.run
        assert kwargs["task_queue"] == TASK_QUEUE
        key = kwargs["id"]
        self.starts.append((key, dict(spec)))
        if self.failure:
            raise ConnectionError("startup request may have been transmitted")
        self.existing[key] = "RUNNING"
        if self.accept_then_ack_loss:
            raise ConnectionResetError("ACK lost after server start")
        return SimpleNamespace(id=key)

    def get_workflow_handle(self, key):
        return FakeHandle(self.existing, key)


def bridge(client):
    async def factory():
        return client
    return TemporalLaunchBridge(
        address="127.0.0.1:7233", client_factory=factory, interval_seconds=300,
    )


def test_registration_atomically_enqueues_once_and_scopes_launch(tmp_path):
    store = registered(tmp_path)
    record = store.launch(user_id=USER, goal_id=GOAL)
    assert record["state"] == "PENDING"
    again = store.register(
        user_id=USER, goal_id=GOAL,
        repo="avaargsh/agent-control-plane", number=169,
    )
    assert again.version == 0
    assert store.launch(user_id=USER, goal_id=GOAL) == record
    assert len(store.pending_launches()) == 1
    with pytest.raises(WatchError, match="not found"):
        store.launch(user_id="bob", goal_id=GOAL)


def test_registration_starts_once_even_under_concurrent_http_like_requests(tmp_path):
    store = registered(tmp_path)
    client = FakeClient()
    sender = bridge(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(
            lambda _: sender.start_committed(
                GoalStore(store.path), user_id=USER, goal_id=GOAL,
            ), range(4),
        ))
    assert len(client.starts) == 1
    assert client.starts[0][1]["user_id"] == USER
    assert all(item["state"] in ("IN_FLIGHT", "ACKED") for item in outcomes)
    assert store.launch(user_id=USER, goal_id=GOAL)["state"] == "ACKED"
    assert sender.start_committed(
        store, user_id=USER, goal_id=GOAL,
    )["state"] == "ACKED"
    assert len(client.starts) == 1


def test_lost_start_ack_is_unknown_no_auto_retry_and_describe_proves_it(tmp_path):
    store = registered(tmp_path)
    client = FakeClient()
    client.accept_then_ack_loss = True
    launcher = bridge(client)
    assert launcher.start_committed(
        store, user_id=USER, goal_id=GOAL,
    )["state"] == "UNKNOWN"
    assert len(client.starts) == 1
    # Neither a second POST nor startup reconciliation repeats a start.
    assert launcher.start_committed(
        store, user_id=USER, goal_id=GOAL,
    )["state"] == "UNKNOWN"
    report = launcher.recover_on_exclusive_startup(GoalStore(store.path))
    assert report["first_start_attempts"] == 0
    assert report["observed_existing"] == 1
    status = store.launch(user_id=USER, goal_id=GOAL)
    assert status["state"] == "OBSERVED"
    assert status["workflow_status"] == "RUNNING"
    assert len(client.starts) == 1


def test_lost_start_ack_with_no_describe_proof_remains_unknown(tmp_path):
    store = registered(tmp_path)
    client = FakeClient()
    client.failure = True
    launcher = bridge(client)
    assert launcher.start_committed(
        store, user_id=USER, goal_id=GOAL,
    )["state"] == "UNKNOWN"
    assert launcher.reconcile(
        store, user_id=USER, goal_id=GOAL,
    )["state"] == "UNKNOWN"
    assert launcher.recover_on_exclusive_startup(store)["first_start_attempts"] == 0
    assert len(client.starts) == 1
    # No accidental claim of start success from a missing Temporal history.
    assert store.launch(user_id=USER, goal_id=GOAL)["evidence"] == "START_UNCERTAIN"


def test_crash_between_claim_and_call_is_conservatively_recovered(tmp_path):
    store = registered(tmp_path)
    assert store.claim_launch(user_id=USER, goal_id=GOAL)["state"] == "IN_FLIGHT"
    client = FakeClient()
    result = bridge(client).recover_on_exclusive_startup(GoalStore(store.path))
    assert result["crash_ambiguities"] == 1
    assert result["first_start_attempts"] == 0
    assert store.launch(user_id=USER, goal_id=GOAL)["state"] == "UNKNOWN"
    assert client.starts == []


def test_paused_goal_can_enroll_without_reactivating_github(tmp_path):
    store = registered(tmp_path)
    store.transition(user_id=USER, goal_id=GOAL, action="pause", expected_version=0)
    client = FakeClient()
    status = bridge(client).start_committed(store, user_id=USER, goal_id=GOAL)
    assert status["state"] == "ACKED"
    assert store.get(user_id=USER, goal_id=GOAL).state == "PAUSED"
    assert len(client.starts) == 1


def test_cancelled_goal_is_not_enrolled(tmp_path):
    store = registered(tmp_path)
    store.transition(user_id=USER, goal_id=GOAL, action="cancel", expected_version=0)
    client = FakeClient()
    status = bridge(client).start_committed(store, user_id=USER, goal_id=GOAL)
    assert status["state"] == "SKIPPED"
    assert status["evidence"] == "GOAL_INACTIVE"
    assert client.starts == []


def test_startup_sweep_attempts_safe_pending_only_and_limits_scope(tmp_path):
    store = registered(tmp_path)
    store.register(
        user_id="bob", goal_id="another",
        repo="avaargsh/agent-control-plane", number=170,
    )
    client = FakeClient()
    result = bridge(client).recover_on_exclusive_startup(store)
    assert result["first_start_attempts"] == 2
    assert result["unresolved"] == 0
    assert len(client.starts) == 2
    assert store.launch(user_id="bob", goal_id="another")["state"] == "ACKED"


def test_real_temporal_duplicate_id_is_observed_not_started_twice(tmp_path):
    async def scenario():
        store = registered(tmp_path)
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async def factory():
                return env.client
            launcher = TemporalLaunchBridge(
                address="127.0.0.1:7233", client_factory=factory,
            )
            # Start the exact Workflow in the actual Temporal test server as
            # if the first API process accepted a response but lost DB ACK.
            remote = await env.client.start_workflow(
                PRWatchWorkflow.run,
                {"user_id": USER, "goal_id": GOAL, "interval_seconds": 300},
                id=workflow_id(user_id=USER, goal_id=GOAL),
                task_queue=TASK_QUEUE,
            )
            assert remote.id == workflow_id(user_id=USER, goal_id=GOAL)
            result = await asyncio.to_thread(
                launcher.start_committed, store, user_id=USER, goal_id=GOAL,
            )
            assert result["state"] == "OBSERVED"
            assert result["workflow_status"] == "RUNNING"
            assert store.launch(user_id=USER, goal_id=GOAL)["evidence"] == "DESCRIBE_EXISTS"

    asyncio.run(scenario())


def test_real_temporal_accepts_enrollment_and_read_only_reconcile(tmp_path):
    async def scenario():
        store = registered(tmp_path)
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async def factory():
                return env.client
            launcher = TemporalLaunchBridge(
                address="127.0.0.1:7233", client_factory=factory,
            )
            outcome = await asyncio.to_thread(
                launcher.start_committed, store, user_id=USER, goal_id=GOAL,
            )
            assert outcome["state"] == "ACKED"
            observed = await asyncio.to_thread(
                launcher.reconcile, store, user_id=USER, goal_id=GOAL,
            )
            assert observed["state"] == "OBSERVED"
            assert observed["workflow_status"] == "RUNNING"

    asyncio.run(scenario())
