from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Any, Mapping, Protocol

from .factory_acceptance import validate_factory_acceptance_artifact


API_VERSION = "aifactory.engineering/v1alpha1"
KIND = "AcceptanceAttestation"
ALGORITHM = "HMAC-SHA256"
_SIGNATURE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FactoryAttestationValidation:
    valid: bool
    trusted: bool
    reason: str | None
    key_id: str | None
    artifact_digest: str | None


class FactoryAttestationVerifier(Protocol):
    def verify(
        self,
        artifact: Mapping[str, Any],
        attestation: Mapping[str, Any],
    ) -> FactoryAttestationValidation:
        ...


def _signature_message(attestation: Mapping[str, Any]) -> bytes:
    payload = {
        "apiVersion": attestation["apiVersion"],
        "kind": attestation["kind"],
        "artifactDigest": attestation["artifactDigest"],
        "keyId": attestation["keyId"],
        "algorithm": attestation["algorithm"],
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class HMACFactoryAttestationVerifier:
    """Reference trust verifier backed by an explicit key-id registry.

    This is suitable for controlled integration/lab proofs. Production systems
    should replace it with an asymmetric/KMS/Sigstore verifier while preserving
    the same verifier boundary.
    """

    def __init__(
        self,
        trusted_keys: Mapping[str, bytes],
    ) -> None:
        self._trusted_keys = {
            str(key_id): bytes(secret)
            for key_id, secret in trusted_keys.items()
            if key_id and secret
        }

    def verify(
        self,
        artifact: Mapping[str, Any],
        attestation: Mapping[str, Any],
    ) -> FactoryAttestationValidation:
        artifact_validation = validate_factory_acceptance_artifact(
            artifact
        )
        if not artifact_validation.valid:
            return FactoryAttestationValidation(
                False,
                False,
                "ARTIFACT_INVALID:"
                + str(artifact_validation.reason),
                None,
                artifact_validation.artifact_digest,
            )

        if attestation.get("apiVersion") != API_VERSION:
            return FactoryAttestationValidation(
                False, False, "API_VERSION_MISMATCH", None,
                artifact_validation.artifact_digest,
            )
        if attestation.get("kind") != KIND:
            return FactoryAttestationValidation(
                False, False, "KIND_MISMATCH", None,
                artifact_validation.artifact_digest,
            )
        if attestation.get("algorithm") != ALGORITHM:
            return FactoryAttestationValidation(
                False, False, "ALGORITHM_UNSUPPORTED", None,
                artifact_validation.artifact_digest,
            )

        key_id = attestation.get("keyId")
        if not isinstance(key_id, str) or not key_id:
            return FactoryAttestationValidation(
                False, False, "KEY_ID_REQUIRED", None,
                artifact_validation.artifact_digest,
            )

        artifact_digest = artifact_validation.artifact_digest
        if attestation.get("artifactDigest") != artifact_digest:
            return FactoryAttestationValidation(
                False, False, "ARTIFACT_DIGEST_MISMATCH", key_id,
                artifact_digest,
            )

        signature = attestation.get("signature")
        if not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
            return FactoryAttestationValidation(
                False, False, "SIGNATURE_INVALID", key_id,
                artifact_digest,
            )

        secret = self._trusted_keys.get(key_id)
        if secret is None:
            return FactoryAttestationValidation(
                False, False, "UNTRUSTED_KEY_ID", key_id,
                artifact_digest,
            )

        expected = hmac.new(
            secret,
            _signature_message(attestation),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return FactoryAttestationValidation(
                False, False, "SIGNATURE_MISMATCH", key_id,
                artifact_digest,
            )

        return FactoryAttestationValidation(
            True,
            True,
            None,
            key_id,
            artifact_digest,
        )


def trusted_factory_acceptance_metrics(
    artifact: Mapping[str, Any],
) -> dict[str, float]:
    return {
        "factory_acceptance_trusted": 1.0,
        "factory_accepted": (
            1.0 if artifact.get("accepted") is True else 0.0
        ),
    }
