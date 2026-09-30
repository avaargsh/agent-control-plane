from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping


API_VERSION = "aifactory.engineering/v1alpha1"
KIND = "AcceptanceArtifact"


@dataclass(frozen=True)
class FactoryAcceptanceValidation:
    valid: bool
    reason: str | None
    artifact_digest: str | None
    integrity_verified: bool
    trusted: bool
    gate_eligible: bool


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(
        char in "0123456789abcdef"
        for char in digest
    )


def validate_factory_acceptance_artifact(
    artifact: Mapping[str, Any],
) -> FactoryAcceptanceValidation:
    """Verify AI Factory AcceptanceArtifact integrity without trusting it.

    A canonical digest proves the payload has not changed since it was sealed.
    It does not establish who produced the artifact. Until an independent
    attestation verifier is wired in, the artifact is evidence-only and must
    not project acceptance fields into release gate metrics.
    """
    if artifact.get("apiVersion") != API_VERSION:
        return FactoryAcceptanceValidation(
            False,
            "API_VERSION_MISMATCH",
            None,
            False,
            False,
            False,
        )
    if artifact.get("kind") != KIND:
        return FactoryAcceptanceValidation(
            False,
            "KIND_MISMATCH",
            None,
            False,
            False,
            False,
        )

    digest = artifact.get("digest")
    if not _is_sha256(digest):
        return FactoryAcceptanceValidation(
            False,
            "DIGEST_INVALID",
            None,
            False,
            False,
            False,
        )

    payload = {
        key: value
        for key, value in artifact.items()
        if key != "digest"
    }
    if _canonical_digest(payload) != digest:
        return FactoryAcceptanceValidation(
            False,
            "DIGEST_MISMATCH",
            str(digest),
            False,
            False,
            False,
        )

    case_id = artifact.get("caseId")
    issued_at = artifact.get("issuedAt")
    disposition = artifact.get("disposition")
    accepted = artifact.get("accepted")
    gates = artifact.get("gates")
    evidence_refs = artifact.get("evidenceRefs")

    if not isinstance(case_id, str) or not case_id:
        return FactoryAcceptanceValidation(
            False,
            "CASE_ID_REQUIRED",
            str(digest),
            True,
            False,
            False,
        )
    if not isinstance(issued_at, str) or not issued_at:
        return FactoryAcceptanceValidation(
            False,
            "ISSUED_AT_REQUIRED",
            str(digest),
            True,
            False,
            False,
        )
    if disposition not in {"ACCEPT", "HOLD", "REJECT"}:
        return FactoryAcceptanceValidation(
            False,
            "DISPOSITION_INVALID",
            str(digest),
            True,
            False,
            False,
        )
    if not isinstance(accepted, bool):
        return FactoryAcceptanceValidation(
            False,
            "ACCEPTED_INVALID",
            str(digest),
            True,
            False,
            False,
        )
    if (disposition == "ACCEPT") != accepted:
        return FactoryAcceptanceValidation(
            False,
            "DISPOSITION_ACCEPTED_MISMATCH",
            str(digest),
            True,
            False,
            False,
        )
    if not isinstance(gates, list) or not gates:
        return FactoryAcceptanceValidation(
            False,
            "GATES_REQUIRED",
            str(digest),
            True,
            False,
            False,
        )
    if not isinstance(evidence_refs, Mapping) or not evidence_refs:
        return FactoryAcceptanceValidation(
            False,
            "EVIDENCE_REFS_REQUIRED",
            str(digest),
            True,
            False,
            False,
        )

    gate_ids: set[str] = set()
    gate_statuses: set[str] = set()
    for gate in gates:
        if not isinstance(gate, Mapping):
            return FactoryAcceptanceValidation(
                False,
                "GATE_INVALID",
                str(digest),
                True,
                False,
                False,
            )
        gate_id = gate.get("gateId")
        if not isinstance(gate_id, str) or not gate_id:
            return FactoryAcceptanceValidation(
                False,
                "GATE_ID_REQUIRED",
                str(digest),
                True,
                False,
                False,
            )
        if gate_id in gate_ids:
            return FactoryAcceptanceValidation(
                False,
                "GATE_ID_DUPLICATE",
                str(digest),
                True,
                False,
                False,
            )
        status = gate.get("status")
        if status not in {
            "PENDING",
            "PASS",
            "WARN",
            "FAIL",
            "ERROR",
            "BLOCKED",
        }:
            return FactoryAcceptanceValidation(
                False,
                "GATE_STATUS_INVALID",
                str(digest),
                True,
                False,
                False,
            )
        gate_reasons = gate.get("reasons")
        if not isinstance(gate_reasons, list) or not all(
            isinstance(item, str)
            for item in gate_reasons
        ):
            return FactoryAcceptanceValidation(
                False,
                "GATE_REASONS_INVALID",
                str(digest),
                True,
                False,
                False,
            )
        gate_ids.add(gate_id)
        gate_statuses.add(str(status))

    reasons = artifact.get("reasons")
    if not isinstance(reasons, list) or not all(
        isinstance(item, str)
        for item in reasons
    ):
        return FactoryAcceptanceValidation(
            False,
            "REASONS_INVALID",
            str(digest),
            True,
            False,
            False,
        )

    if not all(
        isinstance(key, str)
        and key
        and isinstance(value, str)
        and value
        for key, value in evidence_refs.items()
    ):
        return FactoryAcceptanceValidation(
            False,
            "EVIDENCE_REFS_INVALID",
            str(digest),
            True,
            False,
            False,
        )

    if gate_statuses & {"FAIL", "ERROR", "BLOCKED"}:
        expected_disposition = "REJECT"
    elif gate_statuses & {"WARN", "PENDING"}:
        expected_disposition = "HOLD"
    else:
        expected_disposition = "ACCEPT"

    if disposition != expected_disposition:
        return FactoryAcceptanceValidation(
            False,
            "GATE_DISPOSITION_MISMATCH",
            str(digest),
            True,
            False,
            False,
        )

    return FactoryAcceptanceValidation(
        True,
        None,
        str(digest),
        True,
        False,
        False,
    )


def factory_acceptance_evidence(
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    validation = validate_factory_acceptance_artifact(artifact)
    if not validation.valid:
        raise ValueError(validation.reason or "FACTORY_ACCEPTANCE_INVALID")

    return {
        "artifact": dict(artifact),
        "artifact_digest": validation.artifact_digest,
        "integrity_verified": validation.integrity_verified,
        "trusted": validation.trusted,
        "gate_eligible": validation.gate_eligible,
    }
