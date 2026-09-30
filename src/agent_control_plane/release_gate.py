from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .eval_engine import EvalResult, evaluate_gate
from .release_evidence import verify_release_evidence


@dataclass(frozen=True)
class ReleaseGateDecision:
    decision: str
    passed: bool
    release_ref: str | None
    runtime_run_id: str | None
    reason: str
    eval_result: EvalResult


def evaluate_release_gate(
    gate: Mapping[str, Any],
    metrics: Mapping[str, float],
    evidence: Mapping[str, Any],
    *,
    expected_authority_digest: str | None = None,
) -> ReleaseGateDecision:
    """Fail-closed promotion decision over metrics + sealed runtime evidence."""
    release_ref = evidence.get("release_ref")
    runtime_run_id = evidence.get("runtime_run_id")
    empty_eval = EvalResult(False, ())

    if not isinstance(release_ref, str) or not release_ref:
        return ReleaseGateDecision("BLOCK", False, None, runtime_run_id if isinstance(runtime_run_id, str) else None, "MISSING_RELEASE_REF", empty_eval)
    if not isinstance(runtime_run_id, str) or not runtime_run_id:
        return ReleaseGateDecision("BLOCK", False, release_ref, None, "MISSING_RUNTIME_RUN_ID", empty_eval)

    evidence_required = bool(gate.get("spec", {}).get("evidenceRequired", True))
    if evidence_required and not verify_release_evidence(evidence):
        return ReleaseGateDecision("BLOCK", False, release_ref, runtime_run_id, "INVALID_RELEASE_EVIDENCE", empty_eval)

    if (
        expected_authority_digest is not None
        and evidence.get("authority_digest")
        != expected_authority_digest
    ):
        return ReleaseGateDecision(
            "BLOCK", False, release_ref,
            runtime_run_id,
            "AUTHORITY_DIGEST_MISMATCH",
            empty_eval,
        )

    required_provenance = set(
        gate.get("spec", {}).get(
            "requiredProvenance",
            (),
        )
    )
    operation_id = evidence.get("operation_id")
    operation_phase = evidence.get("operation_phase")
    operation_evidence_refs = evidence.get(
        "operation_evidence_refs"
    )
    if "operation" in required_provenance:
        if (
            not isinstance(operation_id, str)
            or not operation_id
        ):
            return ReleaseGateDecision(
                "BLOCK", False, release_ref,
                runtime_run_id,
                "MISSING_OPERATION_ID",
                empty_eval,
            )
        if operation_phase != "VERIFIED":
            return ReleaseGateDecision(
                "BLOCK", False, release_ref,
                runtime_run_id,
                "OPERATION_NOT_VERIFIED",
                empty_eval,
            )
        if (
            not isinstance(
                operation_evidence_refs,
                list,
            )
            or not operation_evidence_refs
        ):
            return ReleaseGateDecision(
                "BLOCK", False, release_ref,
                runtime_run_id,
                "MISSING_OPERATION_EVIDENCE",
                empty_eval,
            )
        if not all(
            isinstance(ref, str) and ref
            for ref in operation_evidence_refs
        ):
            return ReleaseGateDecision(
                "BLOCK", False, release_ref,
                runtime_run_id,
                "INVALID_OPERATION_EVIDENCE",
                empty_eval,
            )

    if "authority" in required_provenance:
        authority_digest = evidence.get(
            "authority_digest"
        )
        operation_approval_id = evidence.get(
            "operation_approval_id"
        )
        if (
            not isinstance(authority_digest, str)
            or not authority_digest.startswith(
                "sha256:"
            )
            or len(authority_digest) != 71
        ):
            return ReleaseGateDecision(
                "BLOCK", False, release_ref,
                runtime_run_id,
                "INVALID_AUTHORITY_DIGEST",
                empty_eval,
            )
        try:
            int(authority_digest[7:], 16)
        except ValueError:
            return ReleaseGateDecision(
                "BLOCK", False, release_ref,
                runtime_run_id,
                "INVALID_AUTHORITY_DIGEST",
                empty_eval,
            )
        if (
            not isinstance(
                operation_approval_id,
                str,
            )
            or not operation_approval_id
        ):
            return ReleaseGateDecision(
                "BLOCK", False, release_ref,
                runtime_run_id,
                "MISSING_OPERATION_APPROVAL_ID",
                empty_eval,
            )

    eval_result = evaluate_gate(gate, metrics)
    if not eval_result.passed:
        failure = str(gate.get("spec", {}).get("onFailure", "block")).upper().replace("-", "_")
        return ReleaseGateDecision(failure, False, release_ref, runtime_run_id, "EVAL_GATE_FAILED", eval_result)

    return ReleaseGateDecision("PROMOTE", True, release_ref, runtime_run_id, "EVAL_GATE_PASSED", eval_result)
