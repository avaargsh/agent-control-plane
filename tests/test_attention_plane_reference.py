from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import sys

import pytest

from agent_control_plane.state_transition_protocol import ProtocolViolation
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from attention_plane import (
    AttentionEvent,
    AttentionOutbox,
    AttentionPreferences,
    DeliveryState,
    Disposition,
    EventKind,
    QuietHours,
    decide,
)


UTC = timezone.utc
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def event(
    event_id="evt-1",
    user_id="user-a",
    kind=EventKind.ACTION_REQUIRED,
    dedupe_key="pr42-ci7",
    summary="PR 42 requires approval",
):
    return AttentionEvent(
        event_id=event_id,
        user_id=user_id,
        goal_id="pr-42",
        kind=kind,
        dedupe_key=dedupe_key,
        summary=summary,
        occurred_at=NOW,
        verified_critical=kind is EventKind.CRITICAL,
    )


def test_reference_policy_quiet_balanced_all():
    success = event(kind=EventKind.TASK_SUCCEEDED)
    routine = event(kind=EventKind.ROUTINE_OK)
    assert decide(routine, AttentionPreferences(mode="all"), now=NOW).disposition is Disposition.SILENT
    assert decide(success, AttentionPreferences(mode="quiet"), now=NOW).disposition is Disposition.SILENT
    assert decide(success, AttentionPreferences(mode="balanced"), now=NOW).disposition is Disposition.DIGEST
    assert decide(success, AttentionPreferences(mode="all"), now=NOW).disposition is Disposition.NOTIFY

    blocked = decide(event(), AttentionPreferences(mode="quiet"), now=NOW)
    assert blocked.disposition is Disposition.NOTIFY
    assert blocked.requires_approval is True


def test_untrusted_critical_and_naive_timestamp_fail_closed():
    with pytest.raises(ProtocolViolation, match="trusted provenance"):
        AttentionEvent(
            event_id="fake", user_id="a", goal_id="g", kind=EventKind.CRITICAL,
            dedupe_key="d", summary="critical", occurred_at=NOW,
        )
    with pytest.raises(ProtocolViolation, match="timezone"):
        decide(event(), AttentionPreferences(), now=datetime(2026, 10, 18))


def test_overnight_quiet_and_verified_critical_override():
    hours = QuietHours(time(22), time(8), "Asia/Shanghai")
    # 16:30 UTC = 00:30 +08 the next morning
    at_night = datetime(2026, 10, 18, 16, 30, tzinfo=UTC)
    pref = AttentionPreferences(mode="all", quiet_hours=hours)
    blocked = decide(event(), pref, now=at_night)
    assert blocked.notify_after == datetime(2026, 10, 19, 0, 0, tzinfo=UTC)

    critical = event(kind=EventKind.CRITICAL)
    default = decide(critical, pref, now=at_night)
    assert default.notify_after == blocked.notify_after
    allowed = decide(
        critical,
        AttentionPreferences(
            mode="all", quiet_hours=hours, allow_critical_during_quiet=True
        ),
        now=at_night,
    )
    assert allowed.notify_after == at_night


def test_non_overnight_quiet_window_and_boundary():
    hours = QuietHours(time(12), time(14), "UTC")
    preferences = AttentionPreferences(mode="all", quiet_hours=hours)
    assert decide(event(), preferences, now=NOW).notify_after == NOW.replace(hour=14)
    after = NOW.replace(hour=14)
    assert decide(event(), preferences, now=after).notify_after == after


def test_persistent_replay_returns_first_decision_and_keeps_single_delivery(tmp_path):
    path = tmp_path / "outbox.db"
    db = AttentionOutbox(path)
    first = db.submit(event(), AttentionPreferences(mode="balanced"), now=NOW)
    replay = AttentionOutbox(path).submit(
        event(), AttentionPreferences(mode="quiet"), now=NOW + timedelta(hours=1),
    )
    assert first == replay
    assert db.count(user_id="user-a", state=DeliveryState.PENDING) == 1

    lease = AttentionOutbox(path).claim_due(user_id="user-a", owner="worker-a", now=NOW)
    assert len(lease) == 1
    assert db.count(user_id="user-a", state=DeliveryState.CLAIMED) == 1


def test_same_event_identity_with_new_payload_rejected(tmp_path):
    db = AttentionOutbox(tmp_path / "outbox.db")
    db.submit(event(), AttentionPreferences(), now=NOW)
    with pytest.raises(ProtocolViolation, match="different payload"):
        db.submit(
            event(summary="altered by hostile source"),
            AttentionPreferences(),
            now=NOW,
        )


def test_dedupe_scoped_to_goal_user_and_episode(tmp_path):
    db = AttentionOutbox(tmp_path / "outbox.db")
    for e in (
        event("first"),
        event("duplicate"),
        event("other-user", user_id="user-b"),
        event("new-incident", dedupe_key="pr42-ci8"),
    ):
        db.submit(e, AttentionPreferences(), now=NOW)
    assert db.count(user_id="user-a", state=DeliveryState.PENDING) == 2
    assert db.count(user_id="user-b", state=DeliveryState.PENDING) == 1


def test_non_notification_decisions_never_enter_delivery_queue(tmp_path):
    db = AttentionOutbox(tmp_path / "outbox.db")
    db.submit(
        event("routine", kind=EventKind.ROUTINE_OK),
        AttentionPreferences(mode="all"),
        now=NOW,
    )
    db.submit(
        event("success", kind=EventKind.TASK_SUCCEEDED),
        AttentionPreferences(mode="balanced"),
        now=NOW,
    )
    assert db.count(user_id="user-a", state=DeliveryState.PENDING) == 0


def test_claims_are_exclusive_and_confirmed_delivery_is_not_redelivered(tmp_path):
    db = AttentionOutbox(tmp_path / "outbox.db")
    db.submit(event(), AttentionPreferences(), now=NOW)
    leases = db.claim_due(user_id="user-a", owner="one", now=NOW)
    assert len(leases) == 1
    assert db.claim_due(user_id="user-a", owner="two", now=NOW) == []
    db.mark_delivered(leases[0], now=NOW + timedelta(seconds=1))
    assert db.count(user_id="user-a", state=DeliveryState.DELIVERED) == 1
    assert db.claim_due(user_id="user-a", owner="two", now=NOW + timedelta(hours=1)) == []


def test_lost_ack_stays_unknown_not_automatically_retried(tmp_path):
    db = AttentionOutbox(tmp_path / "outbox.db")
    db.submit(event(), AttentionPreferences(), now=NOW)
    original = db.claim_due(
        user_id="user-a", owner="crashed-worker", now=NOW, lease_seconds=10
    )[0]
    later = NOW + timedelta(seconds=11)
    assert db.recover_expired(now=later) == 1
    assert db.count(user_id="user-a", state=DeliveryState.UNKNOWN) == 1
    assert db.claim_due(user_id="user-a", owner="other-worker", now=later) == []
    with pytest.raises(ProtocolViolation, match="lease lost"):
        db.mark_delivered(original, now=later)

    with pytest.raises(ProtocolViolation, match="absence evidence"):
        db.requeue_verified_absent(
            user_id="user-a", outbox_id=original.outbox_id, evidence_ref=""
        )
    with pytest.raises(ProtocolViolation, match="not UNKNOWN"):
        db.requeue_verified_absent(
            user_id="user-b", outbox_id=original.outbox_id,
            evidence_ref="provider:no-delivery:123",
        )
    db.requeue_verified_absent(
        user_id="user-a", outbox_id=original.outbox_id,
        evidence_ref="provider:no-delivery:123",
    )
    replacement = db.claim_due(user_id="user-a", owner="verified-worker", now=later)
    assert len(replacement) == 1
    assert replacement[0].generation == original.generation + 1
    with pytest.raises(ProtocolViolation, match="lease lost"):
        db.mark_delivered(original, now=later)


def test_transport_uncertainty_requires_explicit_reconciliation(tmp_path):
    db = AttentionOutbox(tmp_path / "outbox.db")
    db.submit(event(), AttentionPreferences(), now=NOW)
    claimed = db.claim_due(user_id="user-a", owner="worker", now=NOW)[0]
    db.mark_unknown(claimed, now=NOW + timedelta(seconds=1))
    assert db.count(user_id="user-a", state=DeliveryState.UNKNOWN) == 1
    assert db.recover_expired(now=NOW + timedelta(days=2)) == 0
    assert db.claim_due(user_id="user-a", owner="other", now=NOW + timedelta(days=2)) == []


def test_quiet_delivery_due_time_enforced(tmp_path):
    db = AttentionOutbox(tmp_path / "outbox.db")
    preferences = AttentionPreferences(
        mode="all", quiet_hours=QuietHours(time(12), time(14), "UTC")
    )
    db.submit(event(), preferences, now=NOW)
    assert db.claim_due(user_id="user-a", owner="worker", now=NOW) == []
    leases = db.claim_due(
        user_id="user-a", owner="worker", now=NOW.replace(hour=14),
    )
    assert len(leases) == 1
