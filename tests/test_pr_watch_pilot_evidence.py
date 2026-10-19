"""Read-only PR Watch pilot release evidence against disposable PostgreSQL 16."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("psycopg")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from pr_watch_pg_store import PostgresWatchStore  # noqa: E402
from pr_watch_pg_webhook import (  # noqa: E402
    PostgresWebhookIngress, PostgresWebhookSchema,
)
from pr_watch_pilot_evidence import (  # noqa: E402
    EvidenceAuditError, audit_delivery,
)

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")
SECRET = b"operator-evidence-test-" + b"a" * 32
REPO = "avaargsh/agent-control-plane"


@pytest.fixture
def existing():
    if not DSN:
        pytest.skip("disposable PR_WATCH_PG_TEST_DSN required")
    store = PostgresWatchStore(DSN)
    store.install_v2()
    PostgresWebhookSchema(DSN).install()
    goal_id = "proof_" + uuid4().hex
    user_id = "proof_" + uuid4().hex
    store.register(user_id=user_id, goal_id=goal_id, repo=REPO, number=185)
    calls = []
    receiver = PostgresWebhookIngress(
        store=store, user_id=user_id, goal_id=goal_id,
        secret=SECRET, signal=calls.append,
    )
    body = json.dumps({
        "action": "synchronize", "number": 185,
        "repository": {"full_name": REPO},
        "pull_request": {"number": 185, "body": "never include me"},
    }).encode()
    event = {
        "signature": "sha256=" + hmac.new(
            SECRET, body, hashlib.sha256,
        ).hexdigest(),
        "event": "pull_request",
        "delivery": str(uuid4()),
        "body": body,
    }
    assert receiver.accept(**event)["signal_state"] == "ACKED"
    assert len(calls) == 1
    return store, user_id, goal_id, event, calls


def audit(existing, proof=None):
    _, user, goal, event, _ = existing
    return audit_delivery(
        dsn=DSN, user_id=user, goal_id=goal,
        delivery=event["delivery"], prove=proof,
    )


def test_existing_ack_is_not_independent_history_or_release_approval(existing):
    report = audit(existing)
    assert report["inbox_state"] == "ACKED"
    assert report["schema"] == "PG_V2_AND_WEBHOOK_EXTENSION_VERIFIED"
    assert report["signal_identity"] == "DETERMINISTIC_MATCH"
    assert report["temporal_history"] == "NOT_CHECKED"
    assert report["release_decision"] == "NO_GO"
    assert report["actual_github_delivery_origin"] == "NOT_ATTESTED"
    assert report["observations_attributed_to_delivery"] == "NOT_PROVEN"
    assert report["recorded_observations"] == 0
    assert "never include me" not in json.dumps(report)
    assert DSN not in json.dumps(report)
    assert existing[1] not in json.dumps(report)
    assert existing[2] not in json.dumps(report)


def test_positive_and_negative_history_proof_never_mutate_receipt(existing):
    before = audit(existing)
    signal_id = existing[4][0]
    asserted = []
    result = audit(existing, proof=lambda sig: asserted.append(sig) or sig == signal_id)
    assert result["temporal_history"] == "PROVEN"
    assert asserted == [signal_id]
    assert result["release_decision"] == "NO_GO"
    assert audit(existing, proof=lambda _: False)["temporal_history"] == "NOT_PROVEN"
    assert audit(existing, proof=lambda _: 1)["temporal_history"] == "NOT_PROVEN"
    assert audit(existing)["inbox_state"] == before["inbox_state"]
    assert len(existing[4]) == 1


def test_temporal_history_unavailable_is_explicit_not_positive(existing):
    def fails(_):
        raise ConnectionError("private endpoint must not leak")

    report = audit(existing, proof=fails)
    assert report["temporal_history"] == "UNAVAILABLE"
    assert report["release_decision"] == "NO_GO"
    assert "private endpoint" not in json.dumps(report)


def test_scope_and_delivery_selector_fail_closed(existing):
    store, user, goal, event, _ = existing
    for wrong in (
        {"user_id": "another_user"},
        {"goal_id": "another_goal"},
        {"delivery": str(uuid4())},
    ):
        args = {"dsn": DSN, "user_id": user,
                "goal_id": goal, "delivery": event["delivery"]}
        args.update(wrong)
        with pytest.raises(EvidenceAuditError, match="absent"):
            audit_delivery(**args)
    with pytest.raises(EvidenceAuditError, match="canonical delivery"):
        audit_delivery(
            dsn=DSN, user_id=user, goal_id=goal, delivery="../../etc",
        )
    with pytest.raises(EvidenceAuditError, match="canonical scope"):
        audit_delivery(
            dsn=DSN, user_id="../other", goal_id=goal,
            delivery=event["delivery"],
        )
    assert store.goal(user_id=user, goal_id=goal)["state"] == "RUNNING"


def test_tampered_delivery_identity_blocks_audit(existing):
    store, user, goal, event, _ = existing
    with store._connection() as conn:
        conn.execute(
            """UPDATE pr_watch_pg_webhook_wakes SET signal_id='tampered'
               WHERE user_id=%s AND goal_id=%s AND delivery=%s""",
            (user, goal, event["delivery"]),
        )
    with pytest.raises(EvidenceAuditError, match="identity mismatch"):
        audit(existing, proof=lambda _: True)


def test_unknown_receipt_plus_positive_history_still_requires_release_review(existing):
    store, user, goal, event, signals = existing
    with store._connection() as conn:
        conn.execute(
            """UPDATE pr_watch_pg_webhook_wakes SET state='UNKNOWN'
               WHERE user_id=%s AND goal_id=%s AND delivery=%s""",
            (user, goal, event["delivery"]),
        )
    report = audit(existing, proof=lambda sig: sig == signals[0])
    assert report["inbox_state"] == "UNKNOWN"  # audit never mutates
    assert report["temporal_history"] == "PROVEN"
    assert report["release_decision"] == "NO_GO"


def test_cli_sanitizes_connector_errors(monkeypatch, capsys):
    import pr_watch_pilot_evidence as module

    monkeypatch.setenv("PR_WATCH_STORAGE", "postgres")
    monkeypatch.delenv("PR_WATCH_DB", raising=False)
    monkeypatch.setenv("PR_WATCH_PG_DSN", "postgresql://private:password@secret.host/db")
    monkeypatch.setattr(sys, "argv", [
        "pr_watch_pilot_evidence.py", "--user", "alice",
        "--goal", "scope", "--delivery", str(uuid4()),
    ])

    def leak_if_unhandled(**_):
        raise ConnectionError("password=topsecret host=secret.host")

    monkeypatch.setattr(module, "audit_delivery", leak_if_unhandled)
    with pytest.raises(SystemExit) as caught:
        module.main()
    assert caught.value.code != 0
    stderr = capsys.readouterr().err
    assert "evidence unavailable or unverified" in stderr
    assert "topsecret" not in stderr
    assert "secret.host" not in stderr
    assert "password" not in stderr
