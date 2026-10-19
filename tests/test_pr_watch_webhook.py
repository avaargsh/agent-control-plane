"""Signed PR webhook acceptance: HMAC, scope, durable dedupe, uncertain ACK."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from pr_watch_harness import GoalStore
from pr_watch_webhook import (
    GitHubWebhookIngress, WebhookRejected, WebhookServer,
)

SECRET = b"s" * 40
REPO = "avaargsh/agent-control-plane"
NUMBER = 169


def setup(tmp_path, signal=None):
    store = GoalStore(tmp_path / "watch.db")
    store.register(user_id="alice", goal_id="watch", repo=REPO, number=NUMBER)
    seen = []
    if signal is None:
        signal = seen.append
    ingress = GitHubWebhookIngress(
        store=store, user_id="alice", goal_id="watch",
        secret=SECRET, signal=signal,
    )
    return store, ingress, seen


def payload(*, repo=REPO, number=NUMBER, action="synchronize"):
    return json.dumps({
        "action": action,
        "number": number,
        "repository": {"full_name": repo},
        "pull_request": {"number": number, "body": "untrusted webpage instructions"},
    }).encode("utf-8")


def delivery(body=None, *, event="pull_request", guid=None, secret=SECRET):
    raw = body if body is not None else payload()
    return {
        "signature": "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest(),
        "event": event,
        "delivery": guid or str(uuid4()),
        "body": raw,
    }


def journal(store):
    with sqlite3.connect(store.path) as db:
        return db.execute(
            "SELECT delivery,payload_sha256,signal_id,state "
            "FROM pr_watch_webhook_wakes ORDER BY delivery"
        ).fetchall()


def test_signed_event_commits_one_wake_and_replay_stays_idempotent(tmp_path):
    store, ingress, seen = setup(tmp_path)
    event = delivery()
    first = ingress.accept(**event)
    assert first == {"status": "accepted", "signal_state": "ACKED"}
    assert len(seen) == 1 and seen[0].startswith("pr-watch-webhook:")
    assert ingress.accept(**event) == {
        "status": "duplicate", "signal_state": "ACKED",
    }
    # A new process using the same durable DB cannot resend the delivery.
    restored = GitHubWebhookIngress(
        store=GoalStore(store.path), user_id="alice", goal_id="watch",
        secret=SECRET, signal=seen.append,
    )
    assert restored.accept(**event)["status"] == "duplicate"
    assert len(seen) == 1 and len(journal(store)) == 1
    assert b"untrusted webpage instructions" not in repr(journal(store)).encode()


def test_replay_receipt_is_visible_after_goal_paused_or_finished(tmp_path):
    store, ingress, seen = setup(tmp_path)
    event = delivery()
    assert ingress.accept(**event)["signal_state"] == "ACKED"
    store.transition(
        user_id="alice", goal_id="watch", action="cancel", expected_version=0,
    )
    assert ingress.accept(**event) == {
        "status": "duplicate", "signal_state": "ACKED",
    }
    assert ingress.accept(**delivery()) == {"status": "ignored"}
    assert len(seen) == 1 and len(journal(store)) == 1


def test_invalid_signature_and_bad_delivery_reject_before_journal(tmp_path):
    store, ingress, seen = setup(tmp_path)
    good = delivery()
    for signature in ("", "sha1=wrong", "sha256=" + "g" * 64,
                      "sha256=" + "0" * 64):
        with pytest.raises(WebhookRejected) as exc:
            ingress.accept(**{**good, "signature": signature})
        assert exc.value.status == 403
    with pytest.raises(WebhookRejected) as exc:
        ingress.accept(**{**good, "delivery": "../invalid"})
    assert exc.value.status == 400
    assert seen == [] and journal(store) == []


def test_event_not_allowed_to_route_to_other_goal_or_override_state(tmp_path):
    store, ingress, seen = setup(tmp_path)
    for b in (
        payload(repo="other/repo"),
        payload(number=NUMBER + 1),
        payload(action="edited"),
    ):
        assert ingress.accept(**delivery(b))["status"] == "ignored"
    assert ingress.accept(**delivery(payload(), event="push"))["status"] == "ignored"
    store.transition(
        user_id="alice", goal_id="watch", action="pause", expected_version=0,
    )
    assert ingress.accept(**delivery())["status"] == "ignored"
    assert len(seen) == 0 and journal(store) == []


def check_payload(*, kind, related=(NUMBER,), action="completed", repo=REPO):
    return json.dumps({
        "action": action,
        "repository": {"full_name": repo},
        kind: {"name": "untrusted workflow name",
               "pull_requests": [{"number": n} for n in related]},
    }).encode("utf-8")


@pytest.mark.parametrize("kind", ("check_run", "check_suite"))
def test_signed_check_completion_wakes_only_explicitly_associated_pr(tmp_path, kind):
    store, ingress, seen = setup(tmp_path)
    event = delivery(check_payload(kind=kind), event=kind)
    assert ingress.accept(**event)["signal_state"] == "ACKED"
    assert ingress.accept(**event) == {
        "status": "duplicate", "signal_state": "ACKED",
    }
    assert len(seen) == 1 and len(journal(store)) == 1


@pytest.mark.parametrize("kind", ("check_run", "check_suite"))
def test_unassociated_checks_and_fork_missing_pr_ref_do_not_wake(tmp_path, kind):
    store, ingress, seen = setup(tmp_path)
    for raw in (
        check_payload(kind=kind, related=()),
        check_payload(kind=kind, related=(NUMBER + 1,)),
        check_payload(kind=kind, repo="another/repo"),
        check_payload(kind=kind, action="created"),
        json.dumps({
            "action": "completed", "repository": {"full_name": REPO},
            kind: {"head_sha": "a" * 40, "head_branch": "main",
                   "pull_requests": []},
        }).encode("utf-8"),
    ):
        assert ingress.accept(**delivery(raw, event=kind)) == {"status": "ignored"}
    assert len(seen) == 0 and journal(store) == []


@pytest.mark.parametrize("kind", ("check_run", "check_suite"))
def test_ambiguous_check_association_fails_closed_before_journal(tmp_path, kind):
    store, ingress, seen = setup(tmp_path)
    for refs in (None, "not-a-list", [{"number": True}],
                 [{"number": NUMBER}, {"number": "169"}]):
        raw = json.dumps({
            "action": "completed", "repository": {"full_name": REPO},
            kind: {"pull_requests": refs},
        }).encode()
        with pytest.raises(WebhookRejected, match="PR association|associated PR"):
            ingress.accept(**delivery(raw, event=kind))
    assert len(seen) == 0 and journal(store) == []


def test_cross_delivery_id_reuse_rejected_even_when_hmac_is_valid(tmp_path):
    store, ingress, seen = setup(tmp_path)
    guid = str(uuid4())
    ingress.accept(**delivery(guid=guid))
    with pytest.raises(WebhookRejected) as exc:
        ingress.accept(**delivery(payload(action="closed"), guid=guid))
    assert exc.value.status == 409
    assert len(seen) == 1 and len(journal(store)) == 1


def test_unknown_ack_is_not_retried_after_provider_may_have_accepted(tmp_path):
    calls = []

    def lost_ack(identifier):
        calls.append(identifier)
        raise ConnectionError("provider accepted before connection dropped")

    store, ingress, _ = setup(tmp_path, signal=lost_ack)
    event = delivery()
    assert ingress.accept(**event)["signal_state"] == "UNKNOWN"
    assert ingress.accept(**event) == {
        "status": "duplicate", "signal_state": "UNKNOWN",
    }
    assert len(calls) == 1
    assert journal(store)[0][3] == "UNKNOWN"


def test_crash_after_claim_keeps_inflight_and_never_replays(tmp_path):
    calls = []

    def process_crash(identifier):
        calls.append(identifier)
        raise SystemExit("simulated crash after durable claim")

    store, ingress, _ = setup(tmp_path, signal=process_crash)
    event = delivery()
    with pytest.raises(SystemExit):
        ingress.accept(**event)
    restarted = GitHubWebhookIngress(
        store=GoalStore(store.path), user_id="alice", goal_id="watch",
        secret=SECRET, signal=calls.append,
    )
    assert restarted.accept(**event) == {
        "status": "duplicate", "signal_state": "IN_FLIGHT",
    }
    assert len(calls) == 1


def test_parallel_webhooks_single_signal_claim(tmp_path):
    store, ingress, seen = setup(tmp_path)
    same = delivery()
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(lambda _: ingress.accept(**same), range(6)))
    assert sum(x["status"] == "accepted" for x in outcomes) == 1
    assert sum(x["status"] == "duplicate" for x in outcomes) == 5
    assert len(seen) == 1 and len(journal(store)) == 1


def test_bad_json_and_mismatched_pr_number_fail_closed(tmp_path):
    store, ingress, seen = setup(tmp_path)
    for body in (
        b"not json",
        b'{"action":"synchronize"}',
        json.dumps({
            "action": "closed", "number": NUMBER,
            "repository": {"full_name": REPO},
            "pull_request": {"number": NUMBER + 1},
        }).encode(),
    ):
        event = delivery(body)
        if body == b"not json" or b"pull_request" not in body:
            with pytest.raises(WebhookRejected):
                ingress.accept(**event)
        else:
            assert ingress.accept(**event)["status"] == "ignored"
    assert seen == [] and journal(store) == []


@contextmanager
def serve(ingress):
    server = WebhookServer(("127.0.0.1", 0), ingress)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def post(srv, event, *, headers=None, path="/webhooks/github"):
    req_headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": event["signature"],
        "X-GitHub-Event": event["event"],
        "X-GitHub-Delivery": event["delivery"],
        **(headers or {}),
    }
    req = Request(
        "http://127.0.0.1:" + str(srv.server_port) + path,
        data=event["body"], headers=req_headers, method="POST",
    )
    try:
        with urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_real_loopback_http_signed_happy_path_and_bad_signature(tmp_path):
    store, ingress, seen = setup(tmp_path)
    with serve(ingress) as srv:
        event = delivery()
        assert post(srv, event)[0] == 202
        assert post(srv, event)[0] == 200
        assert post(srv, {**event, "signature": "sha256=" + "0" * 64})[0] == 403
        assert post(srv, delivery(), headers={"Origin": "https://evil.example"})[0] == 403
        assert post(srv, delivery(), path="/api/goals")[0] == 404
        assert len(seen) == 1 and len(journal(store)) == 1


def test_loopback_only_and_no_fake_authentication(tmp_path):
    store, ingress, _ = setup(tmp_path)
    with pytest.raises(ValueError, match="loopback"):
        WebhookServer(("0.0.0.0", 0), ingress)
    with pytest.raises(ValueError, match="secret"):
        GitHubWebhookIngress(
            store=store, user_id="alice", goal_id="watch",
            secret=b"short", signal=lambda _: None,
        )
