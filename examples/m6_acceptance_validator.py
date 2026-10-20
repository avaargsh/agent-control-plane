"""Offline-only M6 operator acceptance evidence validator.

No network, credentials, database writes, webhook registration or deployment.
This checks report shape and gates, not authenticity of reported observations.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
GATES = (
    "operator_approval",
    "isolated_environment",
    "pg16_schema_backup_restore",
    "reviewed_tls_ingress",
    "real_github_pull_request_delivery",
    "real_github_check_delivery",
    "raw_body_hmac_verified",
    "durable_inbox_deduplication",
    "temporal_exact_signal_history",
    "independent_github_refetch",
    "owner_scoped_task_activity",
    "ambiguous_ack_reconciliation",
    "negative_security_matrix",
    "worker_restart_recovery",
    "secret_revocation_rollback",
    "independent_reviewer_signoff",
)
OUTCOMES = {"PASS", "FAIL", "NOT_RUN", "BLOCKED"}


def validate(report: object) -> dict:
    errors: list[str] = []
    if not isinstance(report, dict):
        return {"decision": "NO_GO", "errors": ["report must be an object"]}
    if report.get("schema") != "m6-live-acceptance/v1":
        errors.append("unsupported schema")
    if not isinstance(report.get("source_commit"), str) or not SHA40.fullmatch(report["source_commit"]):
        errors.append("source_commit must be a 40-character lowercase git SHA")
    if report.get("environment") != "isolated-disposable":
        errors.append("environment must be isolated-disposable")
    if report.get("github_delivery_kind") != "real-github":
        errors.append("fixture or unspecified GitHub delivery cannot pass")
    if report.get("business_outcome_verified") is not False:
        errors.append("PR Watch projection must not claim authenticated business success")
    if report.get("independent_proof_ref") is not None:
        errors.append("unsigned PR Watch projection must not claim an independent proof")
    gates = report.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates must be an object")
        gates = {}
    if set(gates) != set(GATES):
        errors.append("gate keys must exactly match the pinned acceptance matrix")
    for key in GATES:
        item = gates.get(key)
        if not isinstance(item, dict) or item.get("outcome") not in OUTCOMES:
            errors.append(f"{key}: invalid or missing outcome")
            continue
        if not isinstance(item.get("evidence_sha256"), str) or not HEX64.fullmatch(item["evidence_sha256"]):
            errors.append(f"{key}: missing or invalid evidence SHA-256")
        if not isinstance(item.get("summary"), str) or not item["summary"].strip():
            errors.append(f"{key}: missing non-secret evidence summary")
    if report.get("operator_decision") != "GO":
        errors.append("operator_decision is not GO")
    if report.get("reviewer_decision") != "GO":
        errors.append("reviewer_decision is not GO")
    if any(isinstance(gates.get(k), dict) and gates[k].get("outcome") != "PASS" for k in GATES):
        errors.append("not all gates passed")
    return {"schema": "m6-live-acceptance-verdict/v1", "decision": "NO_GO" if errors else "ELIGIBLE_FOR_INDEPENDENT_REVIEW", "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="operator-produced JSON evidence manifest")
    args = parser.parse_args()
    try:
        result = validate(json.loads(args.report.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        result = {"decision": "NO_GO", "errors": [f"invalid evidence report: {type(exc).__name__}"]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["decision"] == "ELIGIBLE_FOR_INDEPENDENT_REVIEW" else 1


if __name__ == "__main__":
    raise SystemExit(main())
