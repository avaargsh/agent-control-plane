"""PG16 + official GitHub webhook delivery-record corroboration (fixture API)."""
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

from pr_watch_github_delivery_evidence import (  # noqa: E402
    _read_json, audit_github_delivery,
)
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402
from pr_watch_pg_webhook import (  # noqa: E402
    PostgresWebhookIngress, PostgresWebhookSchema,
)
from pr_watch_pilot_evidence import EvidenceAuditError  # noqa: E402

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")
SECRET = b"delivery-test-" + b"z" * 40
REPO = "avaargsh/agent-control-plane"
NUMBER = 185
REPO_ID = 51627
HOOK = 124
DELIVERY_ID = 987654
ENDPOINT = "https://staging.example.org/webhooks/github"


@pytest.fixture
def evidence():
    if not DSN:
        pytest.skip("disposable PR_WATCH_PG_TEST_DSN required")
    store = PostgresWatchStore(DSN)
    store.install_v2()
    PostgresWebhookSchema(DSN).install()
    user = "delivery_" + uuid4().hex
    goal_id = "delivery_" + uuid4().hex
    store.register(user_id=user, goal_id=goal_id, repo=REPO, number=NUMBER)
    raw = json.dumps({
        "action": "synchronize",
        "number": NUMBER,
        "repository": {"full_name": REPO, "id": REPO_ID},
        "pull_request": {"number": NUMBER, "body": "SECRET PR text"},
    }).encode()
    guid = str(uuid4())
    event = {
        "signature": "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest(),
        "event": "pull_request", "delivery": guid, "body": raw,
    }
    signals = []
    incoming = PostgresWebhookIngress(
        store=store, user_id=user, goal_id=goal_id,
        secret=SECRET, signal=signals.append,
    )
    assert incoming.accept(**event)["signal_state"] == "ACKED"
    assert len(signals) == 1
    repository = {"id": REPO_ID, "full_name": REPO}
    record = {
        "id": DELIVERY_ID, "guid": guid, "repository_id": REPO_ID,
        "url": ENDPOINT, "event": "pull_request",
        "action": "synchronize", "status_code": 202, "redelivery": False,
        "request": {
            "headers": {
                "X-GitHub-Delivery": guid, "X-GitHub-Hook-ID": str(HOOK),
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": event["signature"],
            },
            "payload": json.loads(raw),
        },
        "response": {
            "payload": json.dumps({"status": "accepted", "signal_state": "ACKED"}),
        },
    }
    return store, user, goal_id, guid, signals[0], repository, record


def invoke(evidence, *, record=None, repository=None, hook=None, prove=None, prove_once=None):
    _, user, goal, guid, _, canonical_repo, canonical_record = evidence
    repository = canonical_repo if repository is None else repository
    record = canonical_record if record is None else record
    canonical_hook = {
        "type": "Repository", "id": HOOK, "name": "web",
        "active": True, "events": ["pull_request", "check_run", "check_suite"],
        "config": {
            "url": ENDPOINT, "content_type": "json",
            "insecure_ssl": "0",
        },
    }
    hook = canonical_hook if hook is None else hook
    paths = []

    def get(path):
        paths.append(path)
        if path == "":
            return repository
        if path == "/hooks/124":
            return hook
        if path == "/hooks/124/deliveries/987654":
            return record
        pytest.fail("unexpected network path")

    result = audit_github_delivery(
        dsn=DSN, user_id=user, goal_id=goal, guid=guid,
        hook_id=HOOK, delivery_id=DELIVERY_ID,
        expected_endpoint=ENDPOINT, token="not-used-by-injected-fixture",
        prove=prove, prove_once=prove_once, fetch_json=get,
    )
    assert paths == ["", "/hooks/124", "/hooks/124/deliveries/987654"]
    return result


def test_provider_record_plus_pg_claim_corroborates_real_identity_only(evidence):
    output = invoke(evidence, prove=lambda signal: signal == evidence[4])
    assert output["github_delivery_record"] == "MATCHED"
    assert output["github_event"] == "pull_request"
    assert output["actual_github_delivery_origin"] == "GITHUB_API_RECORD_CORROBORATED"
    assert output["temporal_history"] == "PROVEN"
    assert output["inbox_state"] == "ACKED"
    assert output["raw_hmac_body_equivalence"] == "NOT_ATTESTED_BY_API"
    assert output["github_webhook_configuration"] == "CURRENT_ACTIVE_CONFIG_MATCHED"
    assert output["github_webhook_tls_verification"] == "CURRENTLY_REQUIRED"
    assert output["github_webhook_event_scope"] == "EXACT_PR_CHECK_ALLOWLIST"
    assert output["github_webhook_secret_provisioning"] == "NOT_ATTESTED_BY_API"
    assert output["temporal_signal_cardinality"] == "NOT_PROVEN"
    assert output["release_decision"] == "NO_GO"
    text = json.dumps(output)
    assert REPO not in text and ENDPOINT not in text
    assert evidence[1] not in text and evidence[2] not in text
    assert "SECRET PR text" not in text and SECRET.decode() not in text


@pytest.mark.parametrize("field,wrong", [
    ("id", 4), ("guid", "123"), ("repository_id", 99),
    ("url", "https://evil.example/webhooks/github"),
    ("event", "issues"), ("status_code", 500), ("redelivery", "false"),
])
def test_provider_identity_and_ack_mismatch_fail_closed(evidence, field, wrong):
    record = {**evidence[6], field: wrong}
    with pytest.raises(EvidenceAuditError):
        invoke(evidence, record=record)


def test_wrong_website_or_repo_identity_cannot_pass(evidence):
    with pytest.raises(EvidenceAuditError):
        invoke(evidence, repository={"id": 100, "full_name": REPO})
    with pytest.raises(EvidenceAuditError):
        invoke(evidence, repository={"id": REPO_ID, "full_name": "evil/repo"})


def test_missing_or_wrong_pr_association_rejected(evidence):
    original = evidence[6]
    for wrong in (NUMBER+1, True, "185"):
        payload = {**original["request"]["payload"], "number": wrong}
        record = {**original, "request": {
            **original["request"], "payload": payload,
        }}
        with pytest.raises(EvidenceAuditError):
            invoke(evidence, record=record)


@pytest.mark.parametrize("kind", ("check_run", "check_suite"))
def test_check_event_must_include_exact_pr_reference(evidence, kind):
    original = evidence[6]
    def changed(refs):
        return {
            **original, "event": kind, "action": "completed",
            "request": {
                "headers": {
                    **original["request"]["headers"],
                    "X-GitHub-Event": kind,
                },
                "payload": {
                    "action": "completed",
                    "repository": {"full_name": REPO, "id": REPO_ID},
                    kind: {"pull_requests": refs},
                },
            },
        }
    matched = changed([{"number": NUMBER}])
    assert invoke(evidence, record=matched)["pr_association"] == (
        "EXACT_SCOPED_PR_MATCHED"
    )
    for refs in ([], [{"number": NUMBER + 1}], [{"number": True}]):
        with pytest.raises(EvidenceAuditError):
            invoke(evidence, record=changed(refs))


def test_unsupported_ack_body_ignored_event_and_duplicate_headers_rejected(evidence):
    record = evidence[6]
    for payload in (
        '{"status":"ignored"}',
        '{"status":"accepted","signal_state":"NOT_REAL"}',
        '{"status":"accepted","signal_state":"ACKED","token":"leak"}' * 3000,
    ):
        with pytest.raises(EvidenceAuditError):
            invoke(evidence, record={
                **record, "response": {"payload": payload},
            })
    with pytest.raises(EvidenceAuditError, match="ambiguous"):
        invoke(evidence, record={
            **record, "request": {
                **record["request"], "headers": {
                    **record["request"]["headers"],
                    "x-github-delivery": evidence[3],
                },
            },
        })


def test_missing_scoped_inbox_stops_before_github_api_call(evidence):
    store, user, goal, _, _, _, _ = evidence
    calls = []
    with pytest.raises(EvidenceAuditError, match="absent"):
        audit_github_delivery(
            dsn=DSN, user_id=user, goal_id=goal,
            guid=str(uuid4()), hook_id=HOOK, delivery_id=DELIVERY_ID,
            expected_endpoint=ENDPOINT, token="not-called",
            fetch_json=lambda path: calls.append(path),
        )
    assert calls == []
    assert store.goal(user_id=user, goal_id=goal)["state"] == "RUNNING"


def test_provenance_is_still_no_go_when_temporal_history_unavailable(evidence):
    result = invoke(evidence, prove=lambda _: False)
    assert result["temporal_history"] == "NOT_PROVEN"
    assert result["actual_github_delivery_origin"] == "GITHUB_API_RECORD_CORROBORATED"
    assert result["release_decision"] == "NO_GO"


def test_only_fixed_github_api_get_allowed_with_read_token(monkeypatch):
    import pr_watch_github_delivery_evidence as api

    called = []
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *unused):
            return None
        def read(self, max_bytes):
            assert max_bytes == 2_000_001
            return b'{"id": 123, "full_name": "avaargsh/agent-control-plane"}'

    class Opener:
        def open(self, req, timeout):
            called.append((req.full_url, req.get_method(), timeout))
            assert req.get_header("Authorization") == "Bearer " + "x" * 40
            return Response()

    monkeypatch.setattr(api, "build_opener", lambda *handlers: Opener())
    res = _read_json(
        repo=REPO, path="/hooks/124/deliveries/987654", token="x" * 40,
    )
    assert res["id"] == 123
    assert called == [(
        "https://api.github.com/repos/avaargsh/agent-control-plane"
        "/hooks/124/deliveries/987654", "GET", 10,
    )]


@pytest.mark.parametrize("mutate", [
    lambda h: {**h, "id": HOOK + 1},
    lambda h: {**h, "active": False},
    lambda h: {**h, "active": 1},
    lambda h: {**h, "type": "Organization"},
    lambda h: {**h, "name": "push"},
    lambda h: {**h, "events": ["push", "pull_request", "check_run"]},
    lambda h: {**h, "events": ["*"]},
    lambda h: {**h, "events": ["pull_request"]},
    lambda h: {**h, "events": ["check_run"]},
    lambda h: {**h, "events": ["pull_request", "check_run", "check_run"]},
    lambda h: {**h, "events": "pull_request"},
    lambda h: {**h, "config": {
        **h["config"], "url": "https://unexpected.example/webhooks/github",
    }},
    lambda h: {**h, "config": {**h["config"], "insecure_ssl": "1"}},
    lambda h: {**h, "config": {**h["config"], "insecure_ssl": True}},
    lambda h: {**h, "config": {**h["config"], "content_type": "form"}},
    lambda h: {**h, "config": {}},
])
def test_unsafe_or_incomplete_current_github_hook_denies_corroboration(
    evidence, mutate,
):
    hook = {
        "type": "Repository", "id": HOOK, "name": "web", "active": True,
        "events": ["pull_request", "check_run", "check_suite"],
        "config": {
            "url": ENDPOINT, "content_type": "json", "insecure_ssl": "0",
        },
    }
    with pytest.raises(EvidenceAuditError):
        invoke(evidence, hook=mutate(hook))


def test_non_subscribed_delivery_event_is_rejected_even_if_record_exists(evidence):
    hook = {
        "type": "Repository", "id": HOOK, "name": "web", "active": True,
        "events": ["pull_request", "check_run"],
        "config": {
            "url": ENDPOINT, "content_type": "json", "insecure_ssl": "0",
        },
    }
    item = evidence[6]
    record = {
        **item, "event": "check_suite", "action": "completed",
        "request": {
            **item["request"],
            "headers": {
                **item["request"]["headers"], "X-GitHub-Event": "check_suite",
            },
            "payload": {
                "action": "completed",
                "repository": {"full_name": REPO, "id": REPO_ID},
                "check_suite": {"pull_requests": [{"number": NUMBER}]},
            },
        },
    }
    with pytest.raises(EvidenceAuditError, match="events"):
        invoke(evidence, hook=hook, record=record)


def test_temporal_exactly_one_signal_requires_independent_strict_witness(evidence):
    result = invoke(
        evidence, prove_once=lambda signal: signal == evidence[4],
    )
    assert result["temporal_history"] == "PROVEN"
    assert result["temporal_signal_cardinality"] == "EXACTLY_ONE_PROVEN"
    assert result["release_decision"] == "NO_GO"
    for witness in (lambda _: False, lambda _: 1, lambda _: None):
        denied = invoke(evidence, prove_once=witness)
        assert denied["temporal_history"] == "NOT_PROVEN"
        assert denied["temporal_signal_cardinality"] == "NOT_PROVEN"
        assert denied["release_decision"] == "NO_GO"


def test_operator_audit_get_paths_are_strict_allowlist():
    import pr_watch_github_delivery_evidence as api

    for path in (
        "/hooks", "/hooks/0", "/hooks/124/config", "/hooks/124/pings",
        "/pulls/185", "/hooks/-1/deliveries/987654", "/hooks/124/deliveries/0",
        "/hooks/124/deliveries/987654?per_page=100",
        "//evil.example", "/../../../user", "/hooks/124/../secrets",
    ):
        with pytest.raises(EvidenceAuditError, match="unsupported"):
            api._read_json(repo=REPO, path=path, token="x" * 40)
