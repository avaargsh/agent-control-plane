"""Live PostgreSQL v2 consistency, control fencing and Attention acceptance."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("temporalio")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from attention_plane import (  # noqa: E402
    AttentionPreferences, DeliveryState, QuietHours,
)
from pr_watch_harness import PullSnapshot  # noqa: E402
from pr_watch_pg_admission import AdmissionError  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402
from pr_watch_task_projection import project_task  # noqa: E402
from pr_watch_pg_dispatcher import PostgresLaunchDispatcher  # noqa: E402
from pr_watch_launcher import TemporalLaunchBridge  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")
SHA = "d" * 40
NOW = datetime.now(timezone.utc)
REPO = "avaargsh/agent-control-plane"


@pytest.fixture(scope="session")
def database():
    if not DSN:
        pytest.skip("PR_WATCH_PG_TEST_DSN required (disposable PostgreSQL only)")
    db = PostgresWatchStore(DSN)
    db.install_v2()
    # CI's disposable DB only. CASCADE cleans this pilot's FK tables.
    with psycopg.connect(DSN) as conn:
        conn.execute("TRUNCATE pr_watch_pg_goals CASCADE")
    return db


@pytest.fixture
def context(database, request):
    user = "u-" + request.node.name.replace("_", "-")[:76]
    database.register(user_id=user, goal_id="g1", repo=REPO, number=175)
    return database, user


def snapshot(state="OPEN", ci="FAILURE", *, sha=SHA):
    return PullSnapshot(head_sha=sha, state=state, ci=ci)


def observe(db, user, snap=None, **kwargs):
    return db.observe(
        db.goal(user_id=user, goal_id="g1"),
        snap or snapshot(), now=datetime.now(timezone.utc), **kwargs,
    )


def test_v1_and_v2_share_atomic_registered_identity_and_launch(context):
    db, user = context
    goal = db.goal(user_id=user, goal_id="g1")
    assert goal["state"] == "RUNNING" and goal["phase"] == ""
    assert db.launch(user_id=user, goal_id="g1")["state"] == "PENDING"
    assert db.register(user_id=user, goal_id="g1", repo=REPO, number=175)["version"] == 0
    with pytest.raises(AdmissionError, match="not found"):
        db.goal(user_id="not-owner", goal_id="g1")
    with pytest.raises(AdmissionError, match="not found"):
        db.launch(user_id="not-owner", goal_id="g1")


def test_real_pg_task_projection_keeps_unverified_business_outcome(context):
    db, user = context
    before = db.goal(user_id=user, goal_id="g1")
    launch = db.launch(user_id=user, goal_id="g1")
    pending = project_task(before, launch, principal=user)
    assert pending["state"] == "WAITING"
    assert pending["business_outcome_verified"] is False
    assert pending["source_of_truth"] == "pr-watch-goal"
    assert pending["independent_proof_ref"] is None
    assert observe(db, user, snapshot(state="MERGED", ci="UNKNOWN")) == "MERGED"
    completed = project_task(
        db.goal(user_id=user, goal_id="g1"),
        db.launch(user_id=user, goal_id="g1"),
        principal=user,
    )
    assert completed["state"] == "OBSERVED_TERMINAL"
    assert completed["observation_phase"] == "MERGED"
    assert completed["business_outcome_verified"] is False
    assert db.goal(user_id=user, goal_id="g1")["state"] == "FINISHED"


def test_atomic_observation_event_and_notify_in_single_pg_transaction(context):
    db, user = context
    phase = observe(db, user)
    assert phase == "CI_BLOCKED"
    goal = db.goal(user_id=user, goal_id="g1")
    assert goal["phase"] == "CI_BLOCKED" and goal["version"] == 1
    events = db.list_events(user_id=user)
    assert len(events) == 1 and events[0]["kind"] == "ACTION_REQUIRED"
    assert events[0]["emitted"] is True
    counts = db.delivery_counts(user_id=user)
    assert counts == {"PENDING": 1}
    assert observe(db, user) == "UNCHANGED"
    assert db.list_events(user_id=user) == events
    assert db.delivery_counts(user_id=user) == counts
    with psycopg.connect(DSN) as conn:
        decisions = conn.execute(
            """SELECT disposition,requires_approval FROM
               pr_watch_pg_attention_decisions WHERE user_id=%s""",
            (user,),
        ).fetchall()
    assert decisions == [("NOTIFY", True)]


def test_exception_during_attention_decision_rolls_back_every_write(context, monkeypatch):
    db, user = context
    import pr_watch_pg_store
    def outage(*args, **kwargs):
        raise OSError("fault after loading the goal; before commit")
    monkeypatch.setattr(pr_watch_pg_store, "decide", outage)
    with pytest.raises(OSError, match="fault"):
        observe(db, user)
    goal = db.goal(user_id=user, goal_id="g1")
    assert goal["version"] == 0 and goal["signature"] == ""
    assert db.list_events(user_id=user) == []
    assert db.delivery_counts(user_id=user) == {}


def test_pause_during_read_rejects_stale_observation_and_no_outbox(context):
    db, user = context
    captured = db.goal(user_id=user, goal_id="g1")
    db.transition(user_id=user, goal_id="g1", action="pause", expected_version=0)
    assert db.observe(captured, snapshot(), now=NOW) == "INACTIVE"
    assert db.list_events(user_id=user) == []
    assert db.delivery_counts(user_id=user) == {}
    assert db.goal(user_id=user, goal_id="g1")["state"] == "PAUSED"

    class ForbiddenReader:
        def fetch(self, *_):
            raise AssertionError("paused task must not read GitHub")
    assert db.tick(
        user_id=user, goal_id="g1", reader=ForbiddenReader(), now=NOW,
    )["state"] == "PAUSED"


def test_concurrent_controls_are_fenced_and_atomic_with_intents(context):
    db, user = context
    gate = Barrier(6)
    def pause(_):
        gate.wait(timeout=15)
        try:
            db.transition(user_id=user, goal_id="g1", action="pause", expected_version=0)
            return True
        except AdmissionError:
            return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(pause, range(6)))
    assert sum(results) == 1
    controls = db.controls(user_id=user, goal_id="g1")
    assert len(controls) == 1 and controls[0]["action"] == "pause"
    assert controls[0]["version"] == 1
    assert db.goal(user_id=user, goal_id="g1")["version"] == 1
    with pytest.raises(AdmissionError, match="not found"):
        db.control(user_id="bob", goal_id="g1", version=1)


def test_control_signal_unknown_to_history_ack_never_demotes(context):
    db, user = context
    db.transition(user_id=user, goal_id="g1", action="cancel", expected_version=0)
    unknown = db.record_control_signal(
        user_id=user, goal_id="g1", version=1,
        state="UNKNOWN", evidence="ACK_UNCERTAIN",
    )
    assert unknown["signal_state"] == "UNKNOWN"
    ack = db.record_control_signal(
        user_id=user, goal_id="g1", version=1,
        state="ACKED", evidence="HISTORY_PROOF",
    )
    assert ack["signal_state"] == "ACKED"
    still = db.record_control_signal(
        user_id=user, goal_id="g1", version=1,
        state="UNKNOWN", evidence="ACK_UNCERTAIN",
    )
    assert still["signal_state"] == "ACKED"
    with pytest.raises(AdmissionError, match="stale"):
        db.transition(user_id=user, goal_id="g1", action="resume", expected_version=1)


def test_two_users_are_isolated_even_with_same_goal_and_incident(database):
    for user in ("tenant-one", "tenant-two"):
        database.register(user_id=user, goal_id="shared", repo=REPO, number=175)
        database.observe(
            database.goal(user_id=user, goal_id="shared"),
            snapshot(), now=datetime.now(timezone.utc),
        )
    assert len(database.list_events(user_id="tenant-one")) == 1
    assert len(database.list_events(user_id="tenant-two")) == 1
    assert database.delivery_counts(user_id="tenant-one") == {"PENDING": 1}
    assert database.delivery_counts(user_id="tenant-two") == {"PENDING": 1}
    assert database.list_events(
        user_id="tenant-one", after=database.list_events(user_id="tenant-one")[0]["cursor"],
    ) == []


def test_quiet_hours_delay_due_without_silencing_action_required(context):
    db, user = context
    at = datetime.now(timezone.utc)
    start = (at - timedelta(minutes=10)).time().replace(tzinfo=None)
    end = (at + timedelta(minutes=10)).time().replace(tzinfo=None)
    prefs = AttentionPreferences(
        quiet_hours=QuietHours(start, end, "UTC"),
    )
    observe(db, user, preferences=prefs)
    assert db.delivery_counts(user_id=user) == {"PENDING": 1}
    assert db.claim_due(user_id=user, owner="sender") == []


def test_two_pg_connections_cannot_claim_same_notification(context):
    db, user = context
    observe(db, user)
    gate = Barrier(8)
    def take(i):
        gate.wait(timeout=15)
        return db.claim_due(user_id=user, owner="sender-" + str(i), limit=1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(take, range(8)))
    winners = [x[0] for x in results if x]
    assert len(winners) == 1 and winners[0].generation == 1
    assert db.claim_due(user_id=user, owner="another") == []
    assert db.delivery_counts(user_id=user) == {"CLAIMED": 1}


def test_provider_receipt_commits_atomically_and_is_user_scoped(context):
    db, user = context
    observe(db, user)
    claim = db.claim_due(user_id=user, owner="sender")[0]
    with pytest.raises(AdmissionError, match="valid receipt"):
        db.complete_delivery(
            claim, state=DeliveryState.DELIVERED, provider="telegram",
            remote_message_id="123", recipient_digest="bad",
        )
    assert db.delivery_counts(user_id=user) == {"CLAIMED": 1}
    db.complete_delivery(
        claim, state=DeliveryState.DELIVERED, provider="telegram",
        remote_message_id="123", recipient_digest="a" * 64,
    )
    assert db.delivery_counts(user_id=user) == {"DELIVERED": 1}
    receipt = db.receipt(user_id=user, outbox_id=claim.outbox_id)
    assert receipt["provider"] == "telegram"
    assert receipt["remote_message_id"] == "123"
    assert db.receipt(user_id="other", outbox_id=claim.outbox_id) is None
    with pytest.raises(AdmissionError, match="lost"):
        db.complete_delivery(claim, state=DeliveryState.UNKNOWN)


def test_expired_claim_becomes_unknown_without_retry(context):
    db, user = context
    observe(db, user)
    claim = db.claim_due(user_id=user, owner="crashed", lease_seconds=1)[0]
    time.sleep(1.3)
    assert db.recover_expired_deliveries(user_id=user) == 1
    assert db.claim_due(user_id=user, owner="fresh") == []
    assert db.delivery_counts(user_id=user) == {"UNKNOWN": 1}
    with pytest.raises(AdmissionError, match="lost"):
        db.complete_delivery(claim, state=DeliveryState.UNKNOWN)


def test_rollback_on_remote_receipt_id_collision_leaves_claimed(context):
    db, user = context
    observe(db, user, snapshot(sha="a" * 40))
    db.transition(user_id=user, goal_id="g1", action="pause", expected_version=1)
    db.transition(user_id=user, goal_id="g1", action="resume", expected_version=2)
    observe(db, user, snapshot(sha="b" * 40))
    leases = db.claim_due(user_id=user, owner="same-provider", limit=2)
    assert len(leases) == 2
    db.complete_delivery(
        leases[0], state=DeliveryState.DELIVERED, provider="telegram",
        remote_message_id="500", recipient_digest="a" * 64,
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.complete_delivery(
            leases[1], state=DeliveryState.DELIVERED, provider="telegram",
            remote_message_id="500", recipient_digest="a" * 64,
        )
    assert db.delivery_counts(user_id=user) == {"CLAIMED": 1, "DELIVERED": 1}
    db.complete_delivery(leases[1], state=DeliveryState.UNKNOWN)
    assert db.delivery_counts(user_id=user) == {"UNKNOWN": 1, "DELIVERED": 1}


def test_failure_then_green_then_merge_policy_consistency(context):
    db, user = context
    assert observe(db, user) == "CI_BLOCKED"
    assert observe(db, user, snapshot(ci="SUCCESS")) == "CI_CLEARED"
    assert observe(db, user, snapshot(state="MERGED", ci="UNKNOWN")) == "MERGED"
    goal = db.goal(user_id=user, goal_id="g1")
    assert goal["state"] == "FINISHED" and goal["phase"] == "MERGED"
    kinds = [r["kind"] for r in db.list_events(user_id=user)]
    assert kinds == ["ACTION_REQUIRED","TASK_SUCCEEDED","TASK_SUCCEEDED"]
    assert db.delivery_counts(user_id=user) == {"PENDING": 1}
    class ForbiddenReader:
        def fetch(self, *_):
            raise AssertionError("finished task must not fetch")
    assert db.tick(user_id=user,goal_id="g1",reader=ForbiddenReader(),now=NOW)["state"] == "FINISHED"


def test_pg_v2_and_temporal_enrollment_share_exact_goal_identity(context):
    db, user = context
    async def scenario():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async def factory():
                return env.client
            dispatcher = PostgresLaunchDispatcher(
                store=db, temporal=TemporalLaunchBridge(
                    address="test:7233",client_factory=factory,
                ),
            )
            launch = await asyncio.to_thread(
                dispatcher.start_once,user_id=user,goal_id="g1",owner="api-1",
            )
            assert launch["state"] == "ACKED"
            paused = db.transition(
                user_id=user,goal_id="g1",action="pause",expected_version=0,
            )
            assert paused["state"] == "PAUSED"
            assert db.launch(user_id=user,goal_id="g1")["state"] == "ACKED"
    asyncio.run(scenario())
