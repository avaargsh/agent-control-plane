from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .decision_eval import validate_decision_eval_artifact
from .factory_attestation import FactoryAttestationVerifier
from .factory_acceptance import validate_factory_acceptance_artifact


@dataclass(frozen=True)
class LiveProofInputs:
    decision_artifact: Mapping[str, Any]
    factory_artifact: Mapping[str, Any]
    factory_attestation: Mapping[str, Any]


def _contains_synthetic(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return (
            lowered.startswith("synthetic:")
            or lowered.startswith("synthetic://")
            or "contract-fixture" in lowered
            or "synthetic-" in lowered
        )
    if isinstance(value, Mapping):
        return any(
            _contains_synthetic(item)
            for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_synthetic(item) for item in value)
    return False


def validate_live_proof_inputs(
    inputs: LiveProofInputs,
    *,
    factory_verifier: FactoryAttestationVerifier,
) -> dict[str, Any]:
    decision = validate_decision_eval_artifact(
        inputs.decision_artifact
    )
    if not decision.valid:
        raise ValueError(
            "DECISION_ARTIFACT_INVALID:"
            + str(decision.reason)
        )
    if _contains_synthetic(inputs.decision_artifact):
        raise ValueError("DECISION_ARTIFACT_SYNTHETIC")
    fallback = inputs.decision_artifact.get(
        "fallback_evaluation",
        {},
    )
    if fallback.get("measured") is not True:
        raise ValueError("SYSTEM2_FALLBACK_NOT_MEASURED")
    if int(fallback.get("fallback_case_count", 0)) <= 0:
        raise ValueError("SYSTEM2_FALLBACK_NOT_EXERCISED")

    factory = validate_factory_acceptance_artifact(
        inputs.factory_artifact
    )
    if not factory.valid:
        raise ValueError(
            "FACTORY_ARTIFACT_INVALID:"
            + str(factory.reason)
        )
    if _contains_synthetic(inputs.factory_artifact):
        raise ValueError("FACTORY_ARTIFACT_SYNTHETIC")

    attestation = factory_verifier.verify(
        inputs.factory_artifact,
        inputs.factory_attestation,
    )
    if not attestation.valid or not attestation.trusted:
        raise ValueError(
            "FACTORY_ATTESTATION_UNTRUSTED:"
            + str(attestation.reason)
        )

    return {
        "decision_artifact_id": decision.artifact_id,
        "decision_content_digest": decision.content_digest,
        "decision_dataset_digest": (
            inputs.decision_artifact["dataset"]["sha256"]
        ),
        "system2_fallback_case_count": int(
            fallback["fallback_case_count"]
        ),
        "system2_fallback_accuracy": fallback.get("accuracy"),
        "factory_artifact_digest": factory.artifact_digest,
        "factory_attestation_key_id": attestation.key_id,
        "factory_accepted": (
            inputs.factory_artifact.get("accepted") is True
        ),
    }



def assert_live_release_evidence(
    evidence: Mapping[str, Any],
) -> None:
    golden_slice = evidence.get("golden_slice")
    if not isinstance(golden_slice, Mapping):
        raise ValueError("LIVE_GOLDEN_SLICE_EVIDENCE_REQUIRED")
    if _contains_synthetic(golden_slice):
        raise ValueError("LIVE_GOLDEN_SLICE_SYNTHETIC")

    identity = golden_slice.get("identity")
    if not isinstance(identity, Mapping):
        raise ValueError("LIVE_IDENTITY_REQUIRED")
    required_identity = (
        "agentReleaseId",
        "sessionId",
        "runId",
        "temporalWorkflowId",
        "temporalRunId",
        "sandbox",
        "computeWorkload",
    )
    missing = [
        key
        for key in required_identity
        if not identity.get(key)
    ]
    if missing:
        raise ValueError(
            "LIVE_IDENTITY_INCOMPLETE:"
            + ",".join(missing)
        )

    refs = golden_slice.get("refs")
    if not isinstance(refs, Mapping):
        raise ValueError("LIVE_RECEIPTS_REQUIRED")
    for key in (
        "alertEvidence",
        "approvalReceipt",
        "policyDigest",
        "mcpDiagnosticReceipt",
    ):
        if not refs.get(key):
            raise ValueError(
                "LIVE_RECEIPT_MISSING:" + key
            )
