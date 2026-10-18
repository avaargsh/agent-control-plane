"""Negative tests for real Telegram transport adapter and receipt-backed outbox."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from threading import Thread
from urllib.error import URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from attention_plane import (
    AttentionEvent, AttentionOutbox, AttentionPreferences,
    DeliveryState, EventKind, QuietHours,
)
from attention_telegram_adapter import (
    DeliveryConfigurationError, TelegramAdapter, TransportUnknown,
    dispatch_once,
)

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
TOKEN = "12345678:" + "x" * 30
CHATS = {"alice": 12345, "bob": -1005555}


class FakeResponse:
    status = 200

    def __init__(self, value=None, *, status=200, raw=None):
        self.status = status
        self.payload = raw if raw is not None else json.dumps(
            value if value is not None else {
                "ok": True,
                "result": {"message_id": 42, "chat": {"id": CHATS["alice"]}},
            }
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        assert limit <= 65_537
        return self.payload[:limit]


class FakeClient:
    def __init__(self, response=None, fail=None):
        self.response = response or FakeResponse()
        self.fail = fail
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        if self.fail:
            raise self.fail
        return self.response


def adapter(client=None, recipients=None):
    return TelegramAdapter(
        token=TOKEN, recipients=recipients or CHATS,
        http_client=client or FakeClient(),
    )


def submit(db, *, user_id="alice", event_id="evt-1",
           goal_id="pr-42", summary="PR needs review",
           now=NOW, prefs=None):
    db.submit(
        AttentionEvent(
            event_id=event_id, user_id=user_id, goal_id=goal_id,
            dedupe_key=event_id, kind=EventKind.ACTION_REQUIRED,
            summary=summary, occurred_at=now,
        ), prefs or AttentionPreferences(), now=now,
    )


def dispatched(db, sender, *, user_id="alice", now=NOW):
    return dispatch_once(
        db, sender, user_id=user_id, owner="worker-a", now=now,
        clock=lambda: now + timedelta(seconds=1),
    )


def test_telegram_receipt_claims_atomic_acceptance(tmp_path):
    path = tmp_path / "db.sqlite"
    db = AttentionOutbox(path)
    submit(db)
    client = FakeClient()
    result = dispatched(db, adapter(client))
    assert result == {
        "claimed": 1, "accepted_by_provider": 1,
        "uncertain": 0, "expired_to_unknown": 0,
    }
    assert db.count(user_id="alice", state=DeliveryState.DELIVERED) == 1
    # Provider-origin ACK and lease generation survive DB reopens.
    reopened = AttentionOutbox(path)
    with sqlite3.connect(path) as conn:
        outbox_id = conn.execute(
            "SELECT outbox_id FROM deliveries WHERE user_id='alice'"
        ).fetchone()[0]
    receipt = reopened.receipt(user_id="alice", outbox_id=outbox_id)
    assert receipt["provider"] == "telegram"
    assert receipt["remote_message_id"] == "42"
    assert receipt["generation"] == 1
    assert len(receipt["recipient_digest"]) == 64
    assert reopened.receipt(user_id="bob", outbox_id=outbox_id) is None
    assert "12345" not in json.dumps(receipt)
    assert dispatched(db, adapter(client))["claimed"] == 0
    assert len(client.requests) == 1

    request, timeout = client.requests[0]
    assert timeout <= 30
    assert request.get_method() == "POST"
    assert request.full_url.startswith("https://api.telegram.org/bot")
    message = json.loads(request.data)
    assert message == {"chat_id": 12345, "text": "PR needs review"}
    assert "parse_mode" not in message  # No HTML/Markdown injection.


@pytest.mark.parametrize("response", [
    FakeResponse({"ok": False, "description": "error"}),
    FakeResponse({"ok": True, "result": {}}),
    FakeResponse({"ok": True, "result": {"message_id": 42, "chat": {"id": 99}}}),
    FakeResponse({"ok": True, "result": {"message_id": True, "chat": {"id": 12345}}}),
    FakeResponse({"ok": True, "result": {"message_id": 42, "chat": {"id": True}}}),
    FakeResponse(raw=b"{not-json"),
    FakeResponse(status=500),
    FakeResponse(raw=b"x" * 65537),
])
def test_invalid_or_mismatched_provider_receipt_is_unknown(tmp_path, response):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(db)
    result = dispatched(db, adapter(FakeClient(response=response)))
    assert result["accepted_by_provider"] == 0
    assert result["uncertain"] == 1
    assert db.count(user_id="alice", state=DeliveryState.UNKNOWN) == 1
    assert db.receipt(user_id="alice", outbox_id="not-found") is None
    assert dispatched(db, adapter())["claimed"] == 0


def test_transport_timeout_never_replays_without_verified_absence(tmp_path):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(db)
    sender = FakeClient(fail=TimeoutError("token=" + TOKEN))
    assert dispatched(db, adapter(sender))["uncertain"] == 1
    assert db.count(user_id="alice", state=DeliveryState.UNKNOWN) == 1
    assert dispatched(db, adapter(sender), now=NOW + timedelta(hours=2))["claimed"] == 0
    assert len(sender.requests) == 1


def test_network_exception_error_str_does_not_leak_token(tmp_path):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(db)
    lease = db.claim_due(user_id="alice", owner="one", now=NOW)[0]
    sender = adapter(FakeClient(fail=URLError("https://api.telegram.org/bot" + TOKEN)))
    with pytest.raises(TransportUnknown) as caught:
        sender.send(lease)
    assert TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None


def test_unconfigured_identity_does_not_claim_or_recover_other_users(tmp_path):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(db, user_id="alice", event_id="a")
    submit(db, user_id="bob", event_id="b")
    sender = adapter(FakeClient(), recipients={"alice": 12345})
    with pytest.raises(DeliveryConfigurationError):
        dispatched(db, sender, user_id="bob")
    assert db.count(user_id="bob", state=DeliveryState.PENDING) == 1
    dispatched(db, sender)
    assert db.count(user_id="bob", state=DeliveryState.PENDING) == 1
    assert db.count(user_id="alice", state=DeliveryState.DELIVERED) == 1


def test_expired_claim_only_recovered_in_worker_user_scope(tmp_path):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(db, user_id="alice", event_id="a")
    submit(db, user_id="bob", event_id="b")
    db.claim_due(user_id="alice", owner="worker-a", now=NOW, lease_seconds=2)
    db.claim_due(user_id="bob", owner="worker-b", now=NOW, lease_seconds=2)
    sender = adapter(FakeClient())
    result = dispatched(db, sender, now=NOW + timedelta(minutes=1))
    assert result["expired_to_unknown"] == 1
    assert result["claimed"] == 0
    assert db.count(user_id="bob", state=DeliveryState.CLAIMED) == 1
    assert db.count(user_id="alice", state=DeliveryState.UNKNOWN) == 1


def test_slow_provider_response_after_lease_expiry_remains_unclaimed(tmp_path):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(db)
    client = FakeClient()
    result = dispatch_once(
        db, adapter(client), user_id="alice", owner="slow-worker", now=NOW,
        clock=lambda: NOW + timedelta(seconds=95),
    )
    assert result["uncertain"] == 1
    assert db.count(user_id="alice", state=DeliveryState.CLAIMED) == 1
    assert db.recover_expired(now=NOW + timedelta(seconds=96), user_id="alice") == 1
    assert db.count(user_id="alice", state=DeliveryState.UNKNOWN) == 1
    assert len(client.requests) == 1


def test_lost_local_commit_ack_remains_unknown_and_never_retried(tmp_path, monkeypatch):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(db)
    def ack_lost(*args, **kwargs):
        raise OSError("DB acknowledgement lost")
    monkeypatch.setattr(db, "mark_delivered_with_receipt", ack_lost)
    client = FakeClient()
    assert dispatched(db, adapter(client))["uncertain"] == 1
    assert db.count(user_id="alice", state=DeliveryState.UNKNOWN) == 1
    assert len(client.requests) == 1
    assert dispatched(db, adapter(client))["claimed"] == 0


def test_duplicate_provider_message_id_rolls_back_second_receipt(tmp_path):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(db, event_id="first")
    submit(db, event_id="second")
    client = FakeClient()
    result = dispatched(db, adapter(client))
    assert result["claimed"] == 2
    assert result["accepted_by_provider"] == 1
    assert result["uncertain"] == 1
    assert db.count(user_id="alice", state=DeliveryState.DELIVERED) == 1
    assert db.count(user_id="alice", state=DeliveryState.UNKNOWN) == 1
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM delivery_receipts").fetchone()[0] == 1


def test_delivery_receipt_rejects_stale_lease_and_invalid_receipt(tmp_path):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(db)
    lease = db.claim_due(user_id="alice", owner="worker", now=NOW)[0]
    with pytest.raises(Exception):
        db.mark_delivered_with_receipt(
            lease, now=NOW + timedelta(seconds=1),
            provider="not-a-provider", remote_message_id="1",
            recipient_digest="f" * 64,
        )
    with pytest.raises(Exception):
        db.mark_delivered_with_receipt(
            lease, now=NOW + timedelta(seconds=1),
            provider="telegram", remote_message_id="1",
            recipient_digest="12345",
        )
    assert db.count(user_id="alice", state=DeliveryState.CLAIMED) == 1
    db.mark_unknown(lease, now=NOW + timedelta(seconds=1))
    with pytest.raises(Exception):
        db.mark_delivered_with_receipt(
            lease, now=NOW + timedelta(seconds=2),
            provider="telegram", remote_message_id="1",
            recipient_digest="f" * 64,
        )


def test_quiet_window_enforced_before_telegram_send(tmp_path):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(
        db, prefs=AttentionPreferences(
            quiet_hours=QuietHours(time(12), time(14), "UTC")
        ),
    )
    client = FakeClient()
    assert dispatched(db, adapter(client))["claimed"] == 0
    assert len(client.requests) == 0
    assert dispatched(db, adapter(client), now=NOW + timedelta(hours=2))["accepted_by_provider"] == 1


def test_multi_worker_cannot_send_the_same_outbox_intent(tmp_path):
    db = AttentionOutbox(tmp_path / "db.sqlite")
    submit(db)
    client = FakeClient()
    sender = adapter(client)
    results = []
    def deliver():
        results.append(dispatched(AttentionOutbox(db.path), sender))
    workers = [Thread(target=deliver), Thread(target=deliver)]
    for w in workers: w.start()
    for w in workers: w.join()
    assert sum(item["accepted_by_provider"] for item in results) == 1
    assert len(client.requests) == 1


def test_local_config_rejects_untrusted_recipient_mapping():
    with pytest.raises(DeliveryConfigurationError):
        TelegramAdapter(token="short", recipients=CHATS)
    with pytest.raises(DeliveryConfigurationError):
        TelegramAdapter(token=TOKEN, recipients={"alice": True})
    with pytest.raises(DeliveryConfigurationError):
        TelegramAdapter(token=TOKEN, recipients={"bob": 0})
    with pytest.raises(DeliveryConfigurationError):
        TelegramAdapter(token=TOKEN, recipients={"path/user": 12345})
