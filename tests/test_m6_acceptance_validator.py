"""Negative-first tests for offline M6 evidence gate."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from m6_acceptance_validator import GATES, validate


def report():
    return {
        "schema": "m6-live-acceptance/v1",
        "source_commit": "a" * 40,
        "environment": "isolated-disposable",
        "github_delivery_kind": "real-github",
        "business_outcome_verified": False,
        "independent_proof_ref": None,
        "operator_decision": "GO",
        "reviewer_decision": "GO",
        "gates": {
            gate: {"outcome": "PASS", "evidence_sha256": "b" * 64, "summary": "redacted operator observation"}
            for gate in GATES
        },
    }


def test_complete_manifest_is_only_eligible_for_review():
    result = validate(report())
    assert result["decision"] == "ELIGIBLE_FOR_INDEPENDENT_REVIEW"
    assert result["errors"] == []


def test_empty_report_is_no_go():
    assert validate({})["decision"] == "NO_GO"
    assert validate(None)["decision"] == "NO_GO"


def test_fixtures_cannot_claim_live_acceptance():
    item = report()
    item["github_delivery_kind"] = "fixture"
    assert validate(item)["decision"] == "NO_GO"


def test_unknown_or_missing_gate_fails_closed():
    item = report()
    del item["gates"]["real_github_pull_request_delivery"]
    assert validate(item)["decision"] == "NO_GO"
    item = report()
    item["gates"]["surprise"] = item["gates"]["operator_approval"]
    assert validate(item)["decision"] == "NO_GO"


def test_blocked_and_failed_checks_never_go():
    for state in ("FAIL", "BLOCKED", "NOT_RUN"):
        item = report()
        item["gates"]["temporal_exact_signal_history"]["outcome"] = state
        assert validate(item)["decision"] == "NO_GO"


def test_unsigned_outcome_cannot_be_promoted():
    for key, value in (
        ("business_outcome_verified", True),
        ("independent_proof_ref", "sha256:pretend"),
    ):
        item = report()
        item[key] = value
        assert validate(item)["decision"] == "NO_GO"


def test_operator_or_reviewer_no_go_blocks():
    for key in ("operator_decision", "reviewer_decision"):
        item = report()
        item[key] = "NO_GO"
        assert validate(item)["decision"] == "NO_GO"


def test_malformed_evidence_and_source_sha_are_rejected():
    item = report()
    item["source_commit"] = "uncommitted"
    assert validate(item)["decision"] == "NO_GO"
    item = report()
    item["gates"]["raw_body_hmac_verified"]["evidence_sha256"] = "missing"
    assert validate(item)["decision"] == "NO_GO"


def test_cli_missing_file_fails_closed(tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "examples" / "m6_acceptance_validator.py"),
         str(tmp_path / "missing.json")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["decision"] == "NO_GO"

def test_unhashable_nested_gate_status_fails_closed():
    for value in ([], {}, None, 7, True):
        item = report()
        item["gates"]["raw_body_hmac_verified"]["outcome"] = value
        result = validate(item)
        assert result["decision"] == "NO_GO"
        assert any("raw_body_hmac_verified" in error for error in result["errors"])


def test_untrusted_non_object_gate_fails_closed():
    for value in ([], "PASS", None, 3):
        item = report()
        item["gates"]["raw_body_hmac_verified"] = value
        assert validate(item)["decision"] == "NO_GO"

