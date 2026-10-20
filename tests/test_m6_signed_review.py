"""M6 signed reviewer decision verification with ephemeral Ed25519 keys."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from m6_acceptance_validator import GATES
from m6_signed_review import (
    PAYLOAD_TYPE,
    PREDICATE_TYPE,
    STATEMENT_TYPE,
    SignedReviewError,
    TrustedReviewer,
    verify_signed_review,
)

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
SOURCE = "a" * 40
REVIEWER_ID = "security-reviewer-1"
KEY_ID = "m6-review-key-2026"


def _manifest(*, decision="GO", failed_gate=False):
    gates = {}
    for index, gate in enumerate(GATES):
        data = ("fixture-" + str(index)).encode()
        gates[gate] = {
            "outcome": "BLOCKED" if failed_gate and index == 0 else "PASS",
            "summary": "synthetic fixture only",
            "evidence_sha256": hashlib.sha256(data).hexdigest(),
        }
    report = {
        "schema": "m6-live-acceptance/v1",
        "source_commit": SOURCE,
        "environment": "isolated-disposable",
        "github_delivery_kind": "real-github",
        "business_outcome_verified": False,
        "independent_proof_ref": None,
        "operator_decision": "GO",
        "reviewer_decision": decision,
        "gates": gates,
    }
    return json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()


def _pae(payload):
    kind = PAYLOAD_TYPE.encode()
    return (
        b"DSSEv1 " + str(len(kind)).encode() + b" " + kind + b" "
        + str(len(payload)).encode() + b" " + payload
    )


def _case(*, decision="GO", issued=None, expires=None, failed_gate=False):
    manifest = _manifest(decision=decision, failed_gate=failed_gate)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trusted = TrustedReviewer(KEY_ID, REVIEWER_ID, public)
    statement = {
        "_type": STATEMENT_TYPE,
        "subject": [{
            "name": "m6-live-acceptance/manifest.json",
            "digest": {"sha256": hashlib.sha256(manifest).hexdigest()},
        }],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "schema": "m6-signed-review/v1",
            "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            "source_commit": SOURCE,
            "decision": decision,
            "reviewer_id": REVIEWER_ID,
            "issued_at": (issued or NOW - timedelta(seconds=10)).isoformat(),
            "expires_at": (expires or NOW + timedelta(minutes=5)).isoformat(),
        },
    }
    payload = json.dumps(
        statement, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    envelope = {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode(),
        "signatures": [{
            "keyid": KEY_ID,
            "sig": base64.b64encode(private.sign(_pae(payload))).decode(),
        }],
    }
    return manifest, trusted, private, envelope


def _verify(manifest, trusted, envelope, *, source=SOURCE, now=NOW):
    return verify_signed_review(
        manifest_bytes=manifest,
        envelope=envelope,
        trusted_reviewer=trusted,
        expected_source_commit=source,
        now=now,
    )


def test_valid_signed_go_is_authenticated_but_never_releases():
    manifest, trusted, _, envelope = _case()
    result = _verify(manifest, trusted, envelope)
    assert result["decision"] == "SIGNED_GO_VERIFIED_NOT_RELEASED"
    assert result["release_decision"] == "NO_GO"
    assert result["source_commit"] == SOURCE
    assert result["reviewer_id"] == REVIEWER_ID
    assert result["manifest_sha256"] == hashlib.sha256(manifest).hexdigest()
    assert result["business_outcome_verified"] is False
    assert result["independent_proof_ref"] is None


def test_valid_signed_no_go_can_attest_failed_review():
    manifest, trusted, _, envelope = _case(decision="NO_GO", failed_gate=True)
    result = _verify(manifest, trusted, envelope)
    assert result["decision"] == "SIGNED_NO_GO_VERIFIED"
    assert result["release_decision"] == "NO_GO"


def test_changed_manifest_bytes_and_wrong_deployment_commit_denied():
    manifest, trusted, _, envelope = _case()
    with pytest.raises(SignedReviewError, match="subject digest"):
        _verify(manifest + b" ", trusted, envelope)
    with pytest.raises(SignedReviewError, match="trusted deployment"):
        _verify(manifest, trusted, envelope, source="b" * 40)


def test_wrong_reviewer_key_identity_or_keyid_denied():
    manifest, trusted, _, envelope = _case()
    other_key = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    with pytest.raises(SignedReviewError, match="signature verification failed"):
        _verify(manifest, replace(trusted, ed25519_public_key=other_key), envelope)
    with pytest.raises(SignedReviewError, match="pinned trust root"):
        _verify(manifest, replace(trusted, keyid="rotated-key"), envelope)
    with pytest.raises(SignedReviewError, match="pinned profile"):
        _verify(manifest, replace(trusted, reviewer_id="different-reviewer"), envelope)


def test_signed_go_with_blocked_gate_and_conflicting_manifest_decision_denied():
    manifest, trusted, _, envelope = _case(failed_gate=True)
    with pytest.raises(SignedReviewError, match="incomplete or failed"):
        _verify(manifest, trusted, envelope)

    report = json.loads(manifest)
    report["reviewer_decision"] = "NO_GO"
    changed = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(SignedReviewError, match="subject digest"):
        _verify(changed, trusted, envelope)


@pytest.mark.parametrize("issued,expires", [
    (NOW - timedelta(days=2), NOW - timedelta(days=1)),
    (NOW + timedelta(minutes=5), NOW + timedelta(minutes=6)),
    (NOW - timedelta(days=2), NOW + timedelta(minutes=1)),
])
def test_expired_future_or_overlong_signature_denied(issued, expires):
    manifest, trusted, _, envelope = _case(issued=issued, expires=expires)
    with pytest.raises(SignedReviewError, match="expired, future-dated or overlong"):
        _verify(manifest, trusted, envelope)


def test_multiple_signatures_and_tampered_signature_denied():
    manifest, trusted, private, envelope = _case()
    multi = copy.deepcopy(envelope)
    multi["signatures"].append(copy.deepcopy(multi["signatures"][0]))
    with pytest.raises(SignedReviewError, match="exactly one"):
        _verify(manifest, trusted, multi)

    bad = copy.deepcopy(envelope)
    bad["signatures"][0]["sig"] = base64.b64encode(b"x" * 64).decode()
    with pytest.raises(SignedReviewError, match="signature verification failed"):
        _verify(manifest, trusted, bad)


def test_duplicate_signed_json_keys_and_untrusted_predicate_denied():
    manifest, trusted, private, envelope = _case()
    payload = base64.b64decode(envelope["payload"])
    duplicate = payload.replace(b'{"_type":', b'{"_type":"evil","_type":', 1)
    bad = {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(duplicate).decode(),
        "signatures": [{
            "keyid": KEY_ID,
            "sig": base64.b64encode(private.sign(_pae(duplicate))).decode(),
        }],
    }
    with pytest.raises(SignedReviewError, match="duplicate JSON key"):
        _verify(manifest, trusted, bad)

    bad = copy.deepcopy(envelope)
    bad["payloadType"] = "application/json"
    with pytest.raises(SignedReviewError, match="unsupported DSSE envelope"):
        _verify(manifest, trusted, bad)


def test_malformed_trust_profile_clock_and_source_denied():
    manifest, trusted, _, envelope = _case()
    with pytest.raises(SignedReviewError, match="trusted verification time"):
        _verify(manifest, trusted, envelope, now=NOW.replace(tzinfo=None))
    with pytest.raises(SignedReviewError, match="trusted reviewer profile"):
        _verify(manifest, replace(trusted, ed25519_public_key=b"x"), envelope)
    with pytest.raises(SignedReviewError, match="expected source commit"):
        _verify(manifest, trusted, envelope, source="short")
