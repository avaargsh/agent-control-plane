"""Durable authenticated control -> Temporal Signal / reconciliation acceptance."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("temporalio")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from temporalio.testing import WorkflowEnvironment

from pr_watch_control_bridge import (
    TemporalControlBridge, control_signal_id, workflow_id,
)
from pr_watch_harness import GoalStore
from pr_watch_temporal import PRWatchWorkflow

USER = "user-1"
GOAL = "monitor-169"


def setup(tmp_path):
    store = GoalStore(tmp_path / "goal.db")
    store.register(
        user_id=USER, goal_id=GOAL,
        repo="avaargsh/agent-control-plane", number=169,
    )
    return store


def test_control_id_is_scoped_by_user_goal_action_and_version():
    a = control_signal_id(user_id=USER, goal_id=GOAL, version=1, action="pause")
    assert a.startswith("pr-watch-control:")
    assert a != control_signal_id(
        user_id="other", goal_id=GOAL, version=1, action="pause",
    )
    assert a != control_signal_id(
        user_id=USER, goal_id=GOAL, version=1, action="cancel",
    )
    assert workflow_id(user_id=USER, goal_id=GOAL) != workflow_id(
        user_id="other", goal_id=GOAL,
    )


def test_acknowledged_signal_does_not_mutate_goal_or_duplicate_sends(tmp_path):
    store = setup(tmp_path)
    goal = store.transition(
        user_id=USER, goal_id=GOAL, action="pause", expected_version=0,
    )
    requests = []

    class FakeHandle:
        async def signal(self, name, arg):
            requests.append((name, arg))
    class FakeClient:
        def get_workflow_handle(self, key):
            assert key == workflow_id(user_id=USER, goal_id=GOAL)
            return FakeHandle()
    async def factory():
        return FakeClient()
    bridge = TemporalControlBridge(address="test:7233", client_factory=factory)
    result = bridge.signal_committed(
        store, user_id=USER, goal_id=GOAL, version=goal.version,
    )
    assert result["signal_state"] == "ACKED"
    assert result["evidence"] == "SIGNAL_ACK"
    assert requests == [("wake", control_signal_id(
        user_id=USER, goal_id=GOAL, version=goal.version, action="pause",
    ))]
    assert bridge.signal_committed(
        store, user_id=USER, goal_id=GOAL, version=goal.version,
    )["signal_state"] == "ACKED"
    assert len(requests) == 1
    assert store.get(user_id=USER, goal_id=GOAL).state == "PAUSED"


def test_transport_ack_loss_retains_unknown_without_blind_resend(tmp_path):
    store = setup(tmp_path)
    goal = store.transition(
        user_id=USER, goal_id=GOAL, action="cancel", expected_version=0,
    )
    requests = []

    class FakeHandle:
        async def signal(self, name, arg):
            requests.append((name, arg))
            raise ConnectionResetError("ACK lost after possible send")
    class FakeClient:
        def get_workflow_handle(self, key):
            return FakeHandle()
    async def factory():
        return FakeClient()
    bridge = TemporalControlBridge(address="test:7233", client_factory=factory)
    assert bridge.signal_committed(
        store, user_id=USER, goal_id=GOAL, version=goal.version,
    )["signal_state"] == "UNKNOWN"
    assert bridge.signal_committed(
        store, user_id=USER, goal_id=GOAL, version=goal.version,
    )["signal_state"] == "UNKNOWN"
    assert len(requests) == 1
    assert store.get(user_id=USER, goal_id=GOAL).state == "CANCELLED"
    with pytest.raises(ValueError, match="not found"):
        store.control(user_id="another-user", goal_id=GOAL, version=1)


def test_real_temporal_signal_history_reconciles_missing_ack(tmp_path):
    async def scenario():
        store = setup(tmp_path)
        goal = store.transition(
            user_id=USER, goal_id=GOAL, action="pause", expected_version=0,
        )
        # A real Temporal test server accepts a Signal without a Worker
        # running. The durable history is independent of the API's ACK.
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async def factory():
                return env.client
            bridge = TemporalControlBridge(
                address="test-in-memory:7233", client_factory=factory,
            )
            handle = await env.client.start_workflow(
                PRWatchWorkflow.run,
                {"user_id": USER, "goal_id": GOAL, "interval_seconds": 86400},
                id=workflow_id(user_id=USER, goal_id=GOAL),
                task_queue="control-history-reconcile",
            )
            token = control_signal_id(
                user_id=USER, goal_id=GOAL, version=1, action="pause",
            )
            await handle.signal("wake", token)
            # Deliberately model lost local ACK after the server accepted it:
            store.record_control_signal(
                user_id=USER, goal_id=GOAL, version=goal.version,
                state="UNKNOWN", evidence="ACK_UNCERTAIN",
            )
            assert await bridge._history_proves(
                user_id=USER, goal_id=GOAL, signal_id=token,
            )
            assert not await bridge._history_proves(
                user_id=USER, goal_id=GOAL, signal_id=token + "-wrong",
            )
            # Reconcile sync wrapper owns a separate event loop. Verify the
            # server-backed async proof and then persist its result.
            status = store.record_control_signal(
                user_id=USER, goal_id=GOAL, version=goal.version,
                state="ACKED", evidence="HISTORY_PROOF",
            )
            assert status["signal_state"] == "ACKED"
            assert status["evidence"] == "HISTORY_PROOF"

    asyncio.run(scenario())


def test_real_temporal_missing_workflow_is_not_signal_ack():
    async def scenario():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async def factory():
                return env.client
            bridge = TemporalControlBridge(
                address="test-in-memory:7233", client_factory=factory,
            )
            assert await bridge._signal(
                user_id=USER, goal_id="not-started",
                version=1, action="pause",
            ) == "NO_WORKFLOW"

    asyncio.run(scenario())


def test_atomic_send_claim_fences_concurrent_callers_and_legacy_pending(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    store = setup(tmp_path)
    committed = store.transition(
        user_id=USER, goal_id=GOAL, action="pause", expected_version=0,
    )
    entered = Event()
    release = Event()
    lock = Lock()
    attempts = []

    class Bridge(TemporalControlBridge):
        async def _signal(self, *, user_id, goal_id, version, action):
            with lock:
                attempts.append((user_id, goal_id, version, action))
            entered.set()
            # Hold network I/O open while a second dispatcher checks CAS.
            await asyncio.to_thread(release.wait, 5)
            return "ACKED"

    bridge = Bridge(address="test:7233")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            bridge.signal_committed, store,
            user_id=USER, goal_id=GOAL, version=committed.version,
        )
        assert entered.wait(timeout=5)
        claimed = store.control(user_id=USER, goal_id=GOAL, version=1)
        assert claimed["signal_state"] == "UNKNOWN"
        assert claimed["evidence"] == "ACK_UNCERTAIN"
        second = pool.submit(
            bridge.signal_committed, store,
            user_id=USER, goal_id=GOAL, version=committed.version,
        )
        assert second.result(timeout=5)["signal_state"] == "UNKNOWN"
        release.set()
        assert first.result(timeout=5)["signal_state"] == "ACKED"
    assert attempts == [(USER, GOAL, 1, "pause")]


def test_keyed_unattempted_claim_only_and_crashed_claim_stays_unknown(tmp_path):
    store = setup(tmp_path)
    rid = "f" * 32
    command = dict(
        user_id=USER, goal_id=GOAL,
        action="pause", expected_version=0, request_id=rid,
    )
    store.prepare_control(**command)
    goal, replayed = store.transition_with_request(**command)
    assert not replayed and goal.version == 1
    untouched = store.control(user_id=USER, goal_id=GOAL, version=1)
    assert untouched["signal_state"] == "PENDING"
    assert untouched["evidence"] == "UNATTEMPTED_V3"
    with pytest.raises(ValueError, match="not found"):
        store.claim_control_signal(
            user_id="wrong-owner", goal_id=GOAL, version=1,
            unattempted_only=True,
        )
    claimed = store.claim_control_signal(
        user_id=USER, goal_id=GOAL, version=1,
        unattempted_only=True,
    )
    assert claimed is not None and claimed["signal_state"] == "UNKNOWN"
    assert claimed["evidence"] == "ACK_UNCERTAIN"
    # Crash here, before Temporal RPC: no second dispatch can claim.
    reopened = GoalStore(tmp_path / "goal.db")
    assert reopened.claim_control_signal(
        user_id=USER, goal_id=GOAL, version=1,
        unattempted_only=True,
    ) is None
    assert reopened.control(user_id=USER, goal_id=GOAL, version=1)[
        "signal_state"
    ] == "UNKNOWN"
