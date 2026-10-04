#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Mapping

from agent_control_plane.independent_verifier import (
    verify_execution_proof_mapping,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "release" / "v0.1-acceptance-artifact-contract.json"
)


def fail(message: str) -> None:
    raise SystemExit(f"v0.1 acceptance artifact violation: {message}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        fail(f"{name} must be an object")
    return value


def verify_sqlite(path: Path) -> None:
    with path.open("rb") as handle:
        if handle.read(16) != b"SQLite format 3\x00":
            fail(f"{path.name} is not a SQLite database")
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA schema_version").fetchone()
    except sqlite3.DatabaseError as exc:
        fail(f"{path.name} cannot be opened read-only: {exc}")


def git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the complete v0.1.0 Kubernetes acceptance artifact set."
    )
    parser.add_argument("artifact_dir")
    parser.add_argument(
        "--evidence-out",
        help="Optional path for a release-evidence JSON summary.",
    )
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir).resolve()
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    required = [artifact_dir / item for item in contract["requiredArtifacts"]]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        fail(f"required artifacts missing: {missing}")

    for database in (
        "execution-journal.db",
        "execution-leases.db",
        "work-context.db",
    ):
        verify_sqlite(artifact_dir / database)

    proof_path = artifact_dir / "independent-execution-proof.json"
    hash_path = artifact_dir / "independent-execution-proof.json.sha256"
    proof_payload = json.loads(proof_path.read_text(encoding="utf-8"))
    trusted_hash = hash_path.read_text(encoding="utf-8").strip()
    proof = verify_execution_proof_mapping(
        proof_payload,
        expected_statement_hash=trusted_hash,
    )

    proof_contract = contract["proof"]
    if set(proof_payload) != set(proof_contract["requiredTopLevelFields"]):
        fail(
            "IndependentExecutionProof top-level fields changed: "
            f"{sorted(proof_payload)}"
        )
    if proof.predicate_type != proof_contract["predicateType"]:
        fail(f"unexpected predicate type: {proof.predicate_type}")
    if proof.statement_version != proof_contract["statementVersion"]:
        fail(f"unexpected statement version: {proof.statement_version}")
    if proof.statement_hash != trusted_hash:
        fail("statement hash file does not match serialized proof")

    predicate = require_mapping(proof_payload.get("predicate"), "predicate")
    if set(predicate) != set(proof_contract["requiredPredicateFields"]):
        fail(
            "IndependentExecutionProof predicate fields changed: "
            f"{sorted(predicate)}"
        )

    plan = require_mapping(predicate.get("transition_plan"), "transition_plan")
    attempt = require_mapping(
        predicate.get("execution_attempt"),
        "execution_attempt",
    )
    observation = require_mapping(
        predicate.get("after_observation"),
        "after_observation",
    )
    observation_state = require_mapping(
        observation.get("state"),
        "after_observation.state",
    )
    ownership = require_mapping(
        observation_state.get("controlPlaneOwnership"),
        "after_observation.state.controlPlaneOwnership",
    )

    expected_ownership = {
        "agent-control-plane.openai.com/operation-id": attempt.get(
            "operation_id"
        ),
        "agent-control-plane.openai.com/action-hash": attempt.get(
            "action_hash"
        ),
        "agent-control-plane.openai.com/transition-hash": attempt.get(
            "transition_hash"
        ),
        "agent-control-plane.openai.com/plan-hash": plan.get("plan_hash"),
        "agent-control-plane.openai.com/authority-reservation-hash": (
            attempt.get("authority_reservation_hash")
        ),
    }
    for key in proof_contract["ownershipAnnotations"]:
        expected = expected_ownership.get(key)
        if not isinstance(expected, str) or not expected:
            fail(f"durable expected ownership value missing for {key}")
        if ownership.get(key) != expected:
            fail(
                f"ownership marker mismatch for {key}: "
                f"{ownership.get(key)!r} != {expected!r}"
            )

    report = require_mapping(
        predicate.get("verification_report"),
        "verification_report",
    )
    conditions = report.get("conditions")
    if not isinstance(conditions, list):
        fail("verification_report.conditions must be a list")
    condition_status = {
        str(item.get("condition_type")): item.get("status")
        for item in conditions
        if isinstance(item, Mapping)
    }
    for condition in proof_contract["requiredVerificationConditions"]:
        if condition_status.get(condition) != "TRUE":
            fail(
                f"required verification condition is not TRUE: {condition}"
            )

    summary = json.loads(
        (artifact_dir / "summary.json").read_text(encoding="utf-8")
    )
    if summary.get("independent_execution_proof_hash") != trusted_hash:
        fail("summary proof hash does not match trusted digest")
    if summary.get("reconcile_status") != "APPLIED":
        fail(f"reconcile status is not APPLIED: {summary.get('reconcile_status')}")
    if summary.get("verification_status") != "SUCCEEDED":
        fail(
            "verification status is not SUCCEEDED: "
            f"{summary.get('verification_status')}"
        )
    if summary.get("ready_replicas") != summary.get("replicas_desired"):
        fail(
            "desired state not reached in summary: "
            f"ready={summary.get('ready_replicas')} "
            f"desired={summary.get('replicas_desired')}"
        )

    json.loads(
        (artifact_dir / "execution-attestation.json").read_text(
            encoding="utf-8"
        )
    )

    artifact_digests = {
        path.name: sha256_file(path)
        for path in required
    }
    evidence = {
        "contractVersion": contract["contractVersion"],
        "sourceCommit": git_commit(),
        "goldenSlice": contract["goldenSlice"],
        "independentExecutionProofStatementHash": trusted_hash,
        "artifactFileDigests": artifact_digests,
        "verified": True,
    }

    if args.evidence_out:
        output = Path(args.evidence_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
