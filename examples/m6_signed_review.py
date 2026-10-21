"""Verify an independently signed M6 review decision; never deploy or release.

The detached DSSE/Ed25519 signature binds the exact manifest bytes and source
commit. The trusted reviewer identity and raw public key must be pinned out
of band. A valid signed GO is only verified approval evidence; this tool always
returns release_decision=NO_GO and performs no network or provider mutation.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from m6_acceptance_validator import validate
from m6_materialized_evidence import verify_materialized

PAYLOAD_TYPE = "application/vnd.in-toto+json"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://agent-control-plane.dev/m6-signed-review/v1"
_MAX_MANIFEST_BYTES = 4_000_000
_MAX_ENVELOPE_BYTES = 65_536
_MAX_CLOCK_SKEW_SECONDS = 30
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}\Z")


class SignedReviewError(ValueError):
    """Malformed, untrusted, expired or mismatched signed review."""


@dataclass(frozen=True)
class TrustedReviewer:
    """Out-of-band reviewer trust profile, never read from the envelope."""

    keyid: str
    reviewer_id: str
    ed25519_public_key: bytes
    max_age_seconds: int = 86_400


def _read_regular(path: Path, limit: int) -> bytes:
    """Read a bounded regular file while refusing a final-component symlink."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise SignedReviewError("platform lacks safe no-follow file opens")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SignedReviewError("input unavailable or symlink denied") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SignedReviewError("regular file required")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    if not data or len(data) > limit:
        raise SignedReviewError("input empty or exceeds size limit")
    return data

def _unique_json(raw: bytes, limit: int) -> dict:
    if type(raw) is not bytes or not raw or len(raw) > limit:
        raise SignedReviewError("invalid or oversized JSON bytes")

    def unique(pairs: list[tuple[str, Any]]) -> dict:
        output: dict = {}
        for key, value in pairs:
            if key in output:
                raise SignedReviewError("duplicate JSON key")
            output[key] = value
        return output

    def reject_constant(_: str) -> None:
        raise SignedReviewError("non-finite JSON number")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except SignedReviewError:
        raise
    except (ValueError, TypeError) as exc:
        raise SignedReviewError("invalid JSON") from exc
    if not isinstance(value, dict):
        raise SignedReviewError("JSON object required")
    return value


def _decode_b64(value: object, limit: int) -> bytes:
    if not isinstance(value, str) or len(value) > (limit * 4 // 3 + 8):
        raise SignedReviewError("invalid or oversized base64")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SignedReviewError("invalid base64") from exc
    if len(result) > limit:
        raise SignedReviewError("decoded value exceeds size limit")
    return result


def _pae(payload: bytes) -> bytes:
    content_type = PAYLOAD_TYPE.encode("utf-8")
    return (
        b"DSSEv1 " + str(len(content_type)).encode("ascii") + b" "
        + content_type + b" " + str(len(payload)).encode("ascii") + b" "
        + payload
    )


def _time(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise SignedReviewError(name + " must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SignedReviewError("invalid " + name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SignedReviewError(name + " must include timezone")
    return parsed.astimezone(timezone.utc)


def verify_signed_review(
    *,
    manifest_bytes: bytes,
    envelope: Mapping[str, Any],
    trusted_reviewer: TrustedReviewer,
    expected_source_commit: str,
    evidence_root: Path,
    now: datetime,
) -> dict:
    """Verify signed review bytes against a separately pinned reviewer trust root.

    The verifier authenticates approval evidence only. It never sets a release
    GO state, even if the reviewer signed GO and every manifest gate is valid.
    """
    if not isinstance(trusted_reviewer, TrustedReviewer) or (
        not isinstance(trusted_reviewer.keyid, str)
        or not _ID.fullmatch(trusted_reviewer.keyid)
        or not isinstance(trusted_reviewer.reviewer_id, str)
        or not _ID.fullmatch(trusted_reviewer.reviewer_id)
        or type(trusted_reviewer.ed25519_public_key) is not bytes
        or len(trusted_reviewer.ed25519_public_key) != 32
        or type(trusted_reviewer.max_age_seconds) is not int
        or not 1 <= trusted_reviewer.max_age_seconds <= 86_400
    ):
        raise SignedReviewError("invalid trusted reviewer profile")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise SignedReviewError("trusted verification time must be timezone aware")
    if not isinstance(expected_source_commit, str) or not _SHA40.fullmatch(
        expected_source_commit
    ):
        raise SignedReviewError("expected source commit must be a full git SHA")

    report = _unique_json(manifest_bytes, _MAX_MANIFEST_BYTES)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if report.get("schema") != "m6-live-acceptance/v1":
        raise SignedReviewError("unsupported M6 manifest schema")
    source_commit = report.get("source_commit")
    if not isinstance(source_commit, str) or not _SHA40.fullmatch(source_commit):
        raise SignedReviewError("invalid source commit in manifest")
    if source_commit != expected_source_commit:
        raise SignedReviewError("manifest source commit differs from trusted deployment")

    if not isinstance(envelope, Mapping) or set(envelope) != {
        "payloadType", "payload", "signatures",
    } or envelope.get("payloadType") != PAYLOAD_TYPE:
        raise SignedReviewError("unsupported DSSE envelope")
    signatures = envelope.get("signatures")
    if not isinstance(signatures, list) or len(signatures) != 1:
        raise SignedReviewError("exactly one reviewer signature is required")
    signature_record = signatures[0]
    if not isinstance(signature_record, Mapping) or set(signature_record) != {
        "keyid", "sig",
    } or signature_record.get("keyid") != trusted_reviewer.keyid:
        raise SignedReviewError("reviewer key is not the pinned trust root")
    payload = _decode_b64(envelope.get("payload"), _MAX_ENVELOPE_BYTES)
    signature = _decode_b64(signature_record.get("sig"), 64)
    if len(signature) != 64:
        raise SignedReviewError("invalid Ed25519 signature length")

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise SignedReviewError("optional cryptography verifier unavailable") from exc
    try:
        Ed25519PublicKey.from_public_bytes(
            trusted_reviewer.ed25519_public_key
        ).verify(signature, _pae(payload))
    except (InvalidSignature, ValueError) as exc:
        raise SignedReviewError("reviewer signature verification failed") from exc

    statement = _unique_json(payload, _MAX_ENVELOPE_BYTES)
    if set(statement) != {"_type", "subject", "predicateType", "predicate"} or (
        statement.get("_type") != STATEMENT_TYPE
        or statement.get("predicateType") != PREDICATE_TYPE
    ):
        raise SignedReviewError("wrong in-toto statement type")
    digest = manifest_sha
    expected_subject = [{
        "name": "m6-live-acceptance/manifest.json",
        "digest": {"sha256": digest},
    }]
    if statement.get("subject") != expected_subject:
        raise SignedReviewError("signed manifest subject digest mismatch")
    predicate = statement.get("predicate")
    required = {
        "schema", "manifest_sha256", "source_commit", "decision",
        "reviewer_id", "issued_at", "expires_at",
    }
    if not isinstance(predicate, dict) or set(predicate) != required:
        raise SignedReviewError("invalid signed review predicate")
    decision = predicate.get("decision")
    if predicate.get("schema") != "m6-signed-review/v1" or (
        not isinstance(decision, str) or decision not in {"GO", "NO_GO"}
    ):
        raise SignedReviewError("unsupported signed review decision")
    if predicate.get("manifest_sha256") != digest:
        raise SignedReviewError("signed manifest digest mismatch")
    if predicate.get("source_commit") != expected_source_commit:
        raise SignedReviewError("signed source commit mismatch")
    if predicate.get("reviewer_id") != trusted_reviewer.reviewer_id:
        raise SignedReviewError("reviewer identity does not match pinned profile")
    if report.get("reviewer_decision") != decision:
        raise SignedReviewError("manifest and signed reviewer decisions disagree")
    if report.get("business_outcome_verified") is not False or (
        report.get("independent_proof_ref") is not None
    ):
        raise SignedReviewError("M6 manifest overclaims business proof")

    issued = _time(predicate.get("issued_at"), "issued_at")
    expires = _time(predicate.get("expires_at"), "expires_at")
    current = now.astimezone(timezone.utc)
    if (
        issued > current + timedelta(seconds=_MAX_CLOCK_SKEW_SECONDS)
        or current >= expires
        or expires <= issued
        or expires - issued > timedelta(seconds=trusted_reviewer.max_age_seconds)
    ):
        raise SignedReviewError("signed review expired, future-dated or overlong")

    if decision == "GO":
        verdict = validate(report)
        if verdict.get("decision") != "ELIGIBLE_FOR_INDEPENDENT_REVIEW":
            raise SignedReviewError("signed GO has incomplete or failed evidence gates")
        materialized = verify_materialized(report, evidence_root)
        if materialized.get("decision") != "FILES_MATCHED_NOT_RELEASED":
            raise SignedReviewError(
                "signed GO evidence files are missing, unsafe or mismatched"
            )
        outcome = "SIGNED_GO_VERIFIED_NOT_RELEASED"
    else:
        outcome = "SIGNED_NO_GO_VERIFIED"

    return {
        "schema": "m6-signed-review-verdict/v1",
        "decision": outcome,
        "release_decision": "NO_GO",
        "business_outcome_verified": False,
        "independent_proof_ref": None,
        "reviewer_keyid": trusted_reviewer.keyid,
        "reviewer_id": trusted_reviewer.reviewer_id,
        "manifest_sha256": digest,
        "source_commit": expected_source_commit,
        "errors": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("envelope", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True,
                        help="directory containing the exact manifest evidence files")
    parser.add_argument("--trusted-public-key", type=Path, required=True,
                        help="32 raw Ed25519 public-key bytes from out-of-band trust")
    parser.add_argument("--trusted-keyid", required=True)
    parser.add_argument("--trusted-reviewer-id", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    args = parser.parse_args()
    try:
        manifest_bytes = _read_regular(args.manifest, _MAX_MANIFEST_BYTES)
        envelope_bytes = _read_regular(args.envelope, _MAX_ENVELOPE_BYTES)
        public_key = _read_regular(args.trusted_public_key, 32)
        if len(public_key) != 32:
            raise SignedReviewError("trusted Ed25519 public key must be 32 bytes")
        envelope = _unique_json(envelope_bytes, _MAX_ENVELOPE_BYTES)
        result = verify_signed_review(
            manifest_bytes=manifest_bytes,
            envelope=envelope,
            trusted_reviewer=TrustedReviewer(
                keyid=args.trusted_keyid,
                reviewer_id=args.trusted_reviewer_id,
                ed25519_public_key=public_key,
            ),
            expected_source_commit=args.expected_source_commit,
            evidence_root=args.evidence_root,
            now=datetime.now(timezone.utc),
        )
    except (OSError, ValueError) as exc:
        result = {
            "schema": "m6-signed-review-verdict/v1",
            "decision": "NO_GO",
            "release_decision": "NO_GO",
            "business_outcome_verified": False,
            "independent_proof_ref": None,
            "errors": [str(exc) if isinstance(exc, SignedReviewError) else
                       "review evidence unavailable or malformed"],
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["decision"] in {
        "SIGNED_GO_VERIFIED_NOT_RELEASED", "SIGNED_NO_GO_VERIFIED",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
