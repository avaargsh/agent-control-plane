"""M6 pair audit: independent reports must agree on scope, not just PASS."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from m6_paired_delivery_evidence import correlate  # noqa: E402

BASE = {
    "schema": "PG_V2_AND_WEBHOOK_EXTENSION_VERIFIED",
    "provider": "GITHUB_REPOSITORY_WEBHOOK_API",
    "github_delivery_record": "MATCHED",
    "actual_github_delivery_origin": "GITHUB_API_RECORD_CORROBORATED",
    "github_webhook_configuration": "CURRENT_ACTIVE_CONFIG_MATCHED",
    "github_webhook_tls_verification": "CURRENTLY_REQUIRED",
    "github_webhook_event_scope": "EXACT_PR_CHECK_ALLOWLIST",
    "github_webhook_secret_provisioning": "NOT_ATTESTED_BY_API",
    "webhook_endpoint": "HTTPS_DESTINATION_MATCHED",
    "pr_association": "EXACT_SCOPED_PR_MATCHED",
    "receiver_http_ack": "RESPONSE_MATCHED",
    "signal_identity": "DETERMINISTIC_MATCH",
    "inbox_state": "ACKED",
    "temporal_history": "PROVEN",
    "temporal_signal_cardinality": "EXACTLY_ONE_PROVEN",
    "raw_hmac_body_equivalence": "NOT_ATTESTED_BY_API",
    "observations_attributed_to_delivery": "NOT_PROVEN",
    "release_decision": "NO_GO",
    "scope_digest": "a" * 64,
    "workflow_digest": "b" * 64,
    "delivery_digest": "c" * 64,
    "github_event": "pull_request",
    "redelivery": False,
    "recorded_observations": 2,
}


def pair():
    return dict(BASE), {**BASE, "github_event": "check_run",
                        "delivery_digest": "d" * 64}


def test_distinct_correlated_reports_remain_no_go():
    pr, check = pair()
    result = correlate(pr, check)
    assert result["correlation"] == "PAIR_CORROBORATED_NOT_RELEASED"
    assert result["release_decision"] == "NO_GO"
    assert result["business_outcome_verified"] is False
    assert result["independent_proof_ref"] is None
    assert result["errors"] == []


@pytest.mark.parametrize(("label", "field", "value"), [
    ("pr", "github_event", "check_suite"),
    ("check", "github_event", "push"),
    ("check", "temporal_signal_cardinality", "NOT_PROVEN"),
    ("check", "temporal_history", "UNAVAILABLE"),
    ("pr", "github_webhook_tls_verification", "DISABLED"),
    ("check", "github_webhook_configuration", "NOT_CHECKED"),
    ("pr", "raw_hmac_body_equivalence", "ATTESTED"),
    ("pr", "github_delivery_record", "NOT_MATCHED"),
    ("check", "receiver_http_ack", "NOT_MATCHED"),
    ("pr", "inbox_state", "UNKNOWN"),
    ("check", "observations_attributed_to_delivery", "PROVEN"),
    ("pr", "release_decision", "GO"),
    ("pr", "delivery_digest", "not-a-digest"),
    ("check", "scope_digest", "f" * 64),
    ("check", "workflow_digest", "f" * 64),
    ("check", "delivery_digest", "c" * 64),
    ("check", "recorded_observations", True),
    ("pr", "schema", "fixture"),
    ("check", "redelivery", "false"),
])
def test_forged_mismatched_or_unproven_delivery_never_correlates(
    label, field, value,
):
    pr, check = pair()
    target = pr if label == "pr" else check
    target[field] = value
    result = correlate(pr, check)
    assert result["correlation"] == "NOT_CORROBORATED"
    assert result["release_decision"] == "NO_GO"
    assert result["errors"]


@pytest.mark.parametrize("bad", [None, [], "text", 42])
def test_untrusted_non_object_is_no_go(bad):
    pr, check = pair()
    assert correlate(bad, check)["correlation"] == "NOT_CORROBORATED"
    assert correlate(pr, bad)["correlation"] == "NOT_CORROBORATED"


def test_offline_cli_success_and_fail_closed(tmp_path):
    pr, check = pair()
    a, b = tmp_path / "pr.json", tmp_path / "check.json"
    a.write_text(json.dumps(pr), encoding="utf-8")
    b.write_text(json.dumps(check), encoding="utf-8")
    cmd = [sys.executable, str(Path(__file__).resolve().parents[1] /
                               "examples" / "m6_paired_delivery_evidence.py"),
           str(a), str(b)]
    ok = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert ok.returncode == 0
    assert json.loads(ok.stdout)["release_decision"] == "NO_GO"
    b.unlink()
    failed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["correlation"] == "NOT_CORROBORATED"
