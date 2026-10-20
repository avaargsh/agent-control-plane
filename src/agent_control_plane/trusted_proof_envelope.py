"""Optional offline DSSE Ed25519 verifier for existing IndependentExecutionProof/v1.

Trust roots and expected operation context come from the caller's independent
authority, NOT from the envelope. This authenticates a signed historical
proof *reference*, not current policy, a fresh observation, or deployment GO.
No mutation, signing service, network, or replay journal is installed.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .independent_verifier import verify_execution_proof_mapping
from .state_transition_protocol import ProtocolViolation

PAYLOAD_TYPE = "application/vnd.in-toto+json"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://agent-control-plane.dev/trusted-execution-reference/v1"
CONTEXT_KEYS = frozenset({
    "tenant_id", "project_id", "cluster_id", "principal_type",
    "principal_subject", "policy_revision", "authorization_hash",
    "operation_id", "resource_uid", "nonce",
})
MAX_PAYLOAD_BYTES = 65536
MAX_CLOCK_SKEW_SECONDS = 30


@dataclass(frozen=True)
class TrustedSigner:
    """Pinned out-of-band key; never populated from the DSSE envelope."""

    keyid: str
    ed25519_public_key: bytes
    max_age_seconds: int = 600


@dataclass(frozen=True)
class AuthenticatedProofReference:
    """Signer-authenticated reference, NOT authorization to execute or GO."""

    proof_id: str
    proof_statement_hash: str
    trusted_signer_keyid: str
    issued_at: datetime
    expires_at: datetime
    nonce: str


def _decode_b64(value: Any, *, limit: int) -> bytes:
    if not isinstance(value, str) or len(value) > (limit * 4 // 3 + 8):
        raise ProtocolViolation("invalid or oversized DSSE base64")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProtocolViolation("invalid DSSE base64") from exc
    if len(data) > limit:
        raise ProtocolViolation("oversized DSSE payload")
    return data


def _unique_json(raw: bytes) -> dict:
    def unique(pairs: list[tuple[str, Any]]) -> dict:
        output: dict = {}
        for key, value in pairs:
            if key in output:
                raise ProtocolViolation("duplicate signed JSON key")
            output[key] = value
        return output

    try:
        result = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ProtocolViolation("non-finite signed JSON value")
            ),
        )
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ProtocolViolation("invalid signed JSON") from exc
    if not isinstance(result, dict):
        raise ProtocolViolation("signed in-toto statement must be an object")
    return result


def dsse_pae(payload: bytes) -> bytes:
    """Standard DSSE pre-authentication encoding for fixed payload type."""
    content_type = PAYLOAD_TYPE.encode("utf-8")
    return (
        b"DSSEv1 " + str(len(content_type)).encode("ascii") + b" "
        + content_type + b" " + str(len(payload)).encode("ascii") + b" "
        + payload
    )


def _aware_time(value: Any, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise ProtocolViolation(f"{name} must be a timestamp string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolViolation(f"invalid {name}") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ProtocolViolation(f"{name} must be timezone aware")
    return result.astimezone(timezone.utc)


def verify_signed_proof_reference(
    *,
    proof_mapping: Mapping[str, Any],
    envelope: Mapping[str, Any],
    trusted_signer: TrustedSigner,
    expected_context: Mapping[str, str],
    now: datetime,
) -> AuthenticatedProofReference:
    """Fail closed unless signature, proof chain and exact context all match.

    Re-verifying the *same* operation/nonce is safe; a second operation must
    have its own nonce and authoritative expected_context. This API does not
    create or reserve nonce state. Caller must persist and fence it for writes.
    """
    if not isinstance(trusted_signer, TrustedSigner) or (
        not isinstance(trusted_signer.keyid, str) or not trusted_signer.keyid
        or type(trusted_signer.ed25519_public_key) is not bytes
        or len(trusted_signer.ed25519_public_key) != 32
        or type(trusted_signer.max_age_seconds) is not int
        or not 1 <= trusted_signer.max_age_seconds <= 3600
    ):
        raise ProtocolViolation("invalid external signer trust profile")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ProtocolViolation("trusted verifier time must be timezone aware")
    if not isinstance(expected_context, Mapping) or (
        set(expected_context) != CONTEXT_KEYS
        or any(not isinstance(v, str) or not v.strip() for v in expected_context.values())
    ):
        raise ProtocolViolation("missing exact authoritative execution context")
    if not isinstance(envelope, Mapping) or set(envelope) != {
        "payloadType", "payload", "signatures",
    } or envelope["payloadType"] != PAYLOAD_TYPE:
        raise ProtocolViolation("unsupported DSSE envelope")
    signatures = envelope["signatures"]
    if not isinstance(signatures, list) or len(signatures) != 1:
        raise ProtocolViolation("exactly one trusted DSSE signature is required")
    sig = signatures[0]
    if not isinstance(sig, Mapping) or set(sig) != {"keyid", "sig"} or (
        sig["keyid"] != trusted_signer.keyid
    ):
        raise ProtocolViolation("DSSE signer is not the pinned trust root")
    payload = _decode_b64(envelope["payload"], limit=MAX_PAYLOAD_BYTES)
    signature = _decode_b64(sig["sig"], limit=64)
    if len(signature) != 64:
        raise ProtocolViolation("invalid Ed25519 signature size")

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise ProtocolViolation("optional cryptography verifier is unavailable") from exc
    try:
        Ed25519PublicKey.from_public_bytes(
            trusted_signer.ed25519_public_key
        ).verify(signature, dsse_pae(payload))
    except (InvalidSignature, ValueError) as exc:
        raise ProtocolViolation("DSSE signer signature verification failed") from exc

    statement = _unique_json(payload)
    if set(statement) != {"_type", "subject", "predicateType", "predicate"} or (
        statement["_type"] != STATEMENT_TYPE
        or statement["predicateType"] != PREDICATE_TYPE
    ):
        raise ProtocolViolation("wrong in-toto statement or predicate type")
    predicate = statement["predicate"]
    if not isinstance(predicate, dict) or set(predicate) != {
        "proof_statement_hash", "context", "issued_at", "expires_at",
    }:
        raise ProtocolViolation("invalid signed predicate")
    context = predicate["context"]
    if not isinstance(context, dict) or context != dict(expected_context):
        raise ProtocolViolation("signed execution context does not match authority")
    issued = _aware_time(predicate["issued_at"], name="issued_at")
    expires = _aware_time(predicate["expires_at"], name="expires_at")
    trusted_now = now.astimezone(timezone.utc)
    if (issued > trusted_now + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS)
            or trusted_now >= expires or expires <= issued
            or expires - issued > timedelta(seconds=trusted_signer.max_age_seconds)):
        raise ProtocolViolation("signed proof reference expired or future dated")

    # Verify the full original v0.1 proof chain; the signed statement is only
    # a versioned authenticity wrapper around its pre-existing statement hash.
    proof = verify_execution_proof_mapping(proof_mapping)
    if predicate["proof_statement_hash"] != proof.statement_hash:
        raise ProtocolViolation("signed proof hash mismatch")
    if not isinstance(proof.statement_hash, str) or not proof.statement_hash.startswith("sha256:"):
        raise ProtocolViolation("unexpected independent proof digest")
    expected_subject = [{
        "name": f"execution-proof/{proof.proof_id}",
        "digest": {"sha256": proof.statement_hash.removeprefix("sha256:")},
    }]
    if statement["subject"] != expected_subject:
        raise ProtocolViolation("signed in-toto subject mismatch")
    attempt = proof.predicate["execution_attempt"]
    authorization = proof.predicate["plan_authorization"]
    if (attempt["operation_id"] != expected_context["operation_id"]
            or attempt["resource_uid"] != expected_context["resource_uid"]
            or authorization["source_authorization_hash"] != expected_context["authorization_hash"]
            or authorization["principal"] != {
                "type": expected_context["principal_type"],
                "subject": expected_context["principal_subject"],
            }):
        raise ProtocolViolation("signed proof operation or principal mismatch")

    return AuthenticatedProofReference(
        proof_id=proof.proof_id,
        proof_statement_hash=proof.statement_hash,
        trusted_signer_keyid=trusted_signer.keyid,
        issued_at=issued,
        expires_at=expires,
        nonce=expected_context["nonce"],
    )
