"""Real disposable PostgreSQL 16 webhook inbox and Temporal proof contract."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("psycopg")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from pr_watch_pg_store import PostgresWatchStore  # noqa: E402
from pr_watch_pg_webhook import (  # noqa: E402
    PostgresWebhookIngress, PostgresWebhookSchema,
)
from pr_watch_webhook_envelope import WebhookRejected  # noqa: E402

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")
REPO = "avaargsh/agent-control-plane"
SECRET = b"pg-inbox-secret-operator-test-" + b"x" * 16


@pytest.fixture
def context():
    if not DSN:
        pytest.skip("disposable PR_WATCH_PG_TEST_DSN required")
    store = PostgresWatchStore(DSN)
    store.install_v2()
    PostgresWebhookSchema(DSN).install()
    PostgresWebhookSchema(DSN).validate()
    goal_id = "webhook-" + uuid4().hex
    store.register(user_id="alice", goal_id=goal_id, repo=REPO, number=169)
    return store, goal_id


def envelope(*, guid=None, action="synchronize", repo=REPO,
             number=169, secret=SECRET):
    raw = json.dumps({
        "action": action, "number": number,
        "repository": {"full_name": repo},
        "pull_request": {"number": number},
    }).encode()
    return {
        "signature": "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest(),
        "event": "pull_request", "delivery": guid or str(uuid4()), "body": raw,
    }


def ingress(store, goal, signal):
    return PostgresWebhookIngress(
        store=store, user_id="alice", goal_id=goal,
        secret=SECRET, signal=signal,
    )


def rows(store, goal):
    with store._connection() as db:
        return db.execute(
            """SELECT delivery::text,signal_id,state,payload_sha256
               FROM pr_watch_pg_webhook_wakes
               WHERE user_id=%s AND goal_id=%s""",
            ("alice", goal),
        ).fetchall()


def test_explicit_schema_install_is_idempotent_and_has_checksum(context):
    store, goal = context
    schema = PostgresWebhookSchema(DSN)
    schema.install()
    schema.validate()
    assert rows(store, goal) == []


def test_schema_drift_rejects_wrong_scoped_primary_key(context):
    store, goal = context
    # Tampering is rolled back in one transaction; no shared CI data is
    # permanently mutated. Existing column names/constraint count alone
    # would incorrectly accept this degraded uniqueness boundary.
    with store._connection() as db:
        db.execute(
            "ALTER TABLE pr_watch_pg_webhook_wakes "
            "DROP CONSTRAINT pr_watch_pg_webhook_wakes_pkey"
        )
        with pytest.raises(ValueError, match="scoped primary key drift"):
            PostgresWebhookSchema._verify(db)
        db.rollback()
    PostgresWebhookSchema(DSN).validate()
    assert rows(store, goal) == []


def test_schema_drift_rejects_nullable_digest_and_type_changes(context):
    store, goal = context
    with store._connection() as db:
        db.execute(
            "ALTER TABLE pr_watch_pg_webhook_wakes "
            "ALTER COLUMN payload_sha256 DROP NOT NULL"
        )
        with pytest.raises(ValueError, match="nullability mismatch"):
            PostgresWebhookSchema._verify(db)
        db.rollback()
    with store._connection() as db:
        db.execute(
            "ALTER TABLE pr_watch_pg_webhook_wakes "
            "ALTER COLUMN payload_sha256 TYPE VARCHAR(64)"
        )
        with pytest.raises(ValueError, match="type/nullability mismatch"):
            PostgresWebhookSchema._verify(db)
        db.rollback()
    PostgresWebhookSchema(DSN).validate()


def test_repeated_webhook_across_independent_postgres_connections(context):
    store, goal = context
    sent = []
    first = ingress(store, goal, sent.append)
    second = ingress(PostgresWatchStore(DSN), goal, sent.append)
    event = envelope()
    assert first.accept(**event) == {"status": "accepted", "signal_state": "ACKED"}
    assert second.accept(**event) == {
        "status": "duplicate", "signal_state": "ACKED",
    }
    assert len(sent) == 1 and len(rows(store, goal)) == 1
    assert rows(store, goal)[0]["signal_id"].startswith("pr-watch-webhook:")


def test_parallel_postgres_claim_one_signal_attempt(context):
    store, goal = context
    sent = []
    event = envelope()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: ingress(PostgresWatchStore(DSN), goal, sent.append)
            .accept(**event),
            range(8),
        ))
    assert sum(r["status"] == "accepted" for r in results) == 1
    assert sum(r["status"] == "duplicate" for r in results) == 7
    assert len(sent) == 1 and len(rows(store, goal)) == 1


def test_ack_loss_never_retries_and_positive_history_witness_recovers(context):
    store, goal = context
    sent = []

    def lost_ack(signal_id):
        sent.append(signal_id)
        raise ConnectionError("lost RPC ack")

    worker = ingress(store, goal, lost_ack)
    event = envelope()
    assert worker.accept(**event)["signal_state"] == "UNKNOWN"
    assert ingress(store, goal, sent.append).accept(**event) == {
        "status": "duplicate", "signal_state": "UNKNOWN",
    }
    assert len(sent) == 1
    assert worker.reconcile(delivery=event["delivery"], prove=lambda _: False) == {
        "signal_state": "UNKNOWN",
    }
    assert worker.reconcile(delivery=event["delivery"], prove=lambda _: 1) == {
        "signal_state": "UNKNOWN",
    }  # strict boolean proof, no truthy shortcut
    assert worker.reconcile(
        delivery=event["delivery"], prove=lambda sid: sid == sent[0],
    ) == {"signal_state": "ACKED"}
    assert rows(store, goal)[0]["state"] == "ACKED"
    assert len(sent) == 1


def test_postgres_crash_after_claim_keeps_in_flight_not_restarted(context):
    store, goal = context
    attempts = []

    def crash(signal_id):
        attempts.append(signal_id)
        raise SystemExit("worker process crashed")

    event = envelope()
    with pytest.raises(SystemExit):
        ingress(store, goal, crash).accept(**event)
    assert ingress(store, goal, attempts.append).accept(**event) == {
        "status": "duplicate", "signal_state": "IN_FLIGHT",
    }
    assert len(attempts) == 1
    assert rows(store, goal)[0]["state"] == "IN_FLIGHT"


def test_terminal_goal_returns_prior_receipt_but_no_new_wake(context):
    store, goal = context
    sent = []
    receiver = ingress(store, goal, sent.append)
    event = envelope()
    assert receiver.accept(**event)["signal_state"] == "ACKED"
    store.transition(user_id="alice", goal_id=goal, action="cancel",
                     expected_version=0)
    assert receiver.accept(**event)["status"] == "duplicate"
    assert receiver.accept(**envelope()) == {"status": "ignored"}
    assert len(sent) == 1


def test_digest_conflict_and_cross_goal_scope(context):
    store, goal = context
    sent = []
    receiver = ingress(store, goal, sent.append)
    event = envelope()
    receiver.accept(**event)
    with pytest.raises(WebhookRejected) as exc:
        receiver.accept(**envelope(guid=event["delivery"], action="closed"))
    assert exc.value.status == 409
    with pytest.raises(WebhookRejected) as exc:
        receiver.reconcile(delivery=str(uuid4()), prove=lambda _: True)
    assert exc.value.status == 404
    assert len(rows(store, goal)) == 1


def test_invalid_signature_and_unrelated_repo_never_touch_postgres(context):
    store, goal = context
    sent = []
    receiver = ingress(store, goal, sent.append)
    event = envelope()
    with pytest.raises(WebhookRejected) as exc:
        receiver.accept(**{**event, "signature": "sha256=" + "0" * 64})
    assert exc.value.status == 403
    assert receiver.accept(**envelope(repo="another/repo")) == {
        "status": "ignored",
    }
    assert rows(store, goal) == [] and sent == []
