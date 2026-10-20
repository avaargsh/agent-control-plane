"""Offline correlation of TWO operator-observed real GitHub delivery audits.

Never opens a network connection or mutates a provider. This checks two
already-produced redacted reports from the read-only GitHub/PG/Temporal
auditor. It does not establish raw HMAC provenance, independent reviewer
signoff, or a release GO. An untrusted JSON report can be fabricated.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_EXPECTED = {
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
    "actual_github_delivery_origin": "GITHUB_API_RECORD_CORROBORATED",
}
_DIGEST_KEYS = (
    "scope_digest", "workflow_digest", "delivery_digest",
)
_CHECK_EVENTS = frozenset(("check_run", "check_suite"))


def correlate(pr: object, check: object) -> dict:
    """Fail closed; never turn claimed JSON into a signed release decision."""
    errors: list[str] = []
    reports = (("pr", pr), ("check", check))
    for label, report in reports:
        if not isinstance(report, dict):
            errors.append(label + ": report must be an object")
            continue
        for key, value in _EXPECTED.items():
            if report.get(key) != value:
                errors.append(label + ": " + key + " not independently corroborated")
        for key in _DIGEST_KEYS:
            if not isinstance(report.get(key), str) or not _HEX.fullmatch(
                report[key]
            ):
                errors.append(label + ": invalid " + key)
        if report.get("schema") != "PG_V2_AND_WEBHOOK_EXTENSION_VERIFIED":
            errors.append(label + ": invalid PostgreSQL schema witness")
        if report.get("github_event") not in (
            ("pull_request",) if label == "pr" else _CHECK_EVENTS
        ):
            errors.append(label + ": wrong GitHub event type")
        if type(report.get("redelivery")) is not bool:
            errors.append(label + ": missing GitHub redelivery metadata")
        if type(report.get("recorded_observations")) is not int or (
            report["recorded_observations"] < 0
        ):
            errors.append(label + ": invalid observation count")
    if isinstance(pr, dict) and isinstance(check, dict):
        for key in ("scope_digest", "workflow_digest"):
            if pr.get(key) != check.get(key):
                errors.append("cross-delivery: " + key + " mismatch")
        if pr.get("delivery_digest") == check.get("delivery_digest"):
            errors.append("cross-delivery: reused delivery identity")
    return {
        "schema": "m6-paired-delivery-verdict/v1",
        "correlation": (
            "PAIR_CORROBORATED_NOT_RELEASED" if not errors else "NOT_CORROBORATED"
        ),
        "release_decision": "NO_GO",
        "business_outcome_verified": False,
        "independent_proof_ref": None,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr_report", type=Path)
    parser.add_argument("check_report", type=Path)
    args = parser.parse_args()
    try:
        pr = json.loads(args.pr_report.read_text(encoding="utf-8"))
        check = json.loads(args.check_report.read_text(encoding="utf-8"))
        result = correlate(pr, check)
    except (OSError, ValueError):
        result = {
            "schema": "m6-paired-delivery-verdict/v1",
            "correlation": "NOT_CORROBORATED",
            "release_decision": "NO_GO",
            "business_outcome_verified": False,
            "independent_proof_ref": None,
            "errors": ["missing or invalid operator evidence file"],
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["correlation"] == "PAIR_CORROBORATED_NOT_RELEASED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
