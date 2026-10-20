"""Offline Ed25519 DSSE trust binding. No deployment, secrets or signing service."""
from __future__ import annotations

import base64
import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_control_plane.state_transition_protocol import ProtocolViolation
from agent_control_plane.trusted_proof_envelope import (
    PREDICATE_TYPE, PAYLOAD_TYPE, STATEMENT_TYPE,
    TrustedSigner, dsse_pae, verify_signed_proof_reference,
)
from test_independent_verifier import _proof_fixture


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def _case(tmp_path):
    proof = _proof_fixture(tmp_path)
    mapping = proof.as_mapping()
    auth = proof.predicate["plan_authorization"]
    attempt = proof.predicate["execution_attempt"]
    context = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "cluster_id": "cluster-a",
        "principal_type": auth["principal"]["type"],
        "principal_subject": auth["principal"]["subject"],
        "policy_revision": "policy-v7",
        "authorization_hash": auth["source_authorization_hash"],
        "operation_id": attempt["operation_id"],
        "resource_uid": attempt["resource_uid"],
        "nonce": "nonce-for-this-immutable-operation-v1",
    }
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signer = TrustedSigner("local-test-key", public)
    statement = {
        "_type": STATEMENT_TYPE,
        "subject": [{
            "name": "execution-proof/" + proof.proof_id,
            "digest": {"sha256": proof.statement_hash.removeprefix("sha256:")},
        }],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "proof_statement_hash": proof.statement_hash,
            "context": context,
            "issued_at": (NOW - timedelta(seconds=10)).isoformat(),
            "expires_at": (NOW + timedelta(minutes=2)).isoformat(),
        },
    }
    return mapping, context, signer, private, statement


def _signed(private, statement):
    payload = json.dumps(
        statement, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    signature = private.sign(dsse_pae(payload))
    return {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode(),
        "signatures": [{
            "keyid": "local-test-key",
            "sig": base64.b64encode(signature).decode(),
        }],
    }


def _verify(mapping, context, signer, envelope, *, now=NOW):
    return verify_signed_proof_reference(
        proof_mapping=mapping, expected_context=context,
        trusted_signer=signer, envelope=envelope, now=now,
    )


def test_authenticates_real_independent_proof_without_modifying_v01(tmp_path):
    mapping, context, signer, private, stmt = _case(tmp_path)
    signed = _signed(private, stmt)
    result = _verify(mapping, context, signer, signed)
    assert result.proof_id == mapping["proof_id"]
    assert result.proof_statement_hash == mapping["statement_hash"]
    assert result.trusted_signer_keyid == "local-test-key"
    assert result.nonce == context["nonce"]
    # Deterministic read-only re-verification of the same operation.
    assert _verify(mapping, context, signer, signed) == result


@pytest.mark.parametrize("field", [
    "tenant_id", "project_id", "cluster_id", "principal_type",
    "principal_subject", "policy_revision", "authorization_hash",
    "operation_id", "resource_uid", "nonce",
])
def test_changed_authoritative_context_cannot_reuse_signature(tmp_path, field):
    mapping, context, signer, private, stmt = _case(tmp_path)
    signed = _signed(private, stmt)
    wrong = {**context, field: "different"}
    with pytest.raises(ProtocolViolation, match="context"):
        _verify(mapping, wrong, signer, signed)


def test_signature_tamper_and_rotated_out_key_denied(tmp_path):
    mapping, context, signer, private, stmt = _case(tmp_path)
    signed = _signed(private, stmt)
    payload = base64.b64decode(signed["payload"])
    signed["payload"] = base64.b64encode(payload + b" ").decode()
    with pytest.raises(ProtocolViolation, match="signature"):
        _verify(mapping, context, signer, signed)
    signed = _signed(private, stmt)
    rotated = replace(
        signer, ed25519_public_key=Ed25519PrivateKey.generate().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
    )
    with pytest.raises(ProtocolViolation, match="signature"):
        _verify(mapping, context, rotated, signed)


def test_unsigned_duplicate_or_ambiguous_signatures_denied(tmp_path):
    mapping, context, signer, private, stmt = _case(tmp_path)
    signed = _signed(private, stmt)
    for variant in (
        {**signed, "signatures": []},
        {**signed, "signatures": signed["signatures"] * 2},
        {**signed, "signatures": [{"keyid": "other", "sig": signed["signatures"][0]["sig"]}]},
        {**signed, "payloadType": "application/json"},
    ):
        with pytest.raises(ProtocolViolation):
            _verify(mapping, context, signer, variant)


def test_signed_wrong_predicate_and_subject_denied(tmp_path):
    mapping, context, signer, private, stmt = _case(tmp_path)
    for edit in (
        lambda doc: doc.update(predicateType="https://untrusted.invalid/predicate"),
        lambda doc: doc["subject"][0]["digest"].update(sha256="f" * 64),
        lambda doc: doc["predicate"].update(proof_statement_hash="sha256:" + "f" * 64),
        lambda doc: doc["predicate"]["context"].update(nonce="unsolicited"),
    ):
        altered = copy.deepcopy(stmt)
        edit(altered)
        with pytest.raises(ProtocolViolation):
            _verify(mapping, context, signer, _signed(private, altered))


def test_expired_future_dated_and_oversized_ttl_denied(tmp_path):
    mapping, context, signer, private, stmt = _case(tmp_path)
    base = copy.deepcopy(stmt)
    for issued, expires in (
        (NOW - timedelta(minutes=5), NOW - timedelta(seconds=1)),
        (NOW + timedelta(minutes=5), NOW + timedelta(minutes=6)),
        (NOW - timedelta(minutes=10), NOW + timedelta(minutes=10)),
    ):
        item = copy.deepcopy(base)
        item["predicate"]["issued_at"] = issued.isoformat()
        item["predicate"]["expires_at"] = expires.isoformat()
        with pytest.raises(ProtocolViolation, match="expired or future"):
            _verify(mapping, context, signer, _signed(private, item))


def test_forged_outer_proof_not_accepted_with_old_signed_statement(tmp_path):
    mapping, context, signer, private, stmt = _case(tmp_path)
    signed = _signed(private, stmt)
    forged = copy.deepcopy(mapping)
    forged["proof_id"] = "different"
    with pytest.raises(ProtocolViolation):
        _verify(forged, context, signer, signed)


def test_malformed_trust_profile_and_clock_denied(tmp_path):
    mapping, context, signer, private, stmt = _case(tmp_path)
    signed = _signed(private, stmt)
    with pytest.raises(ProtocolViolation, match="trust profile"):
        _verify(mapping, context, replace(signer, ed25519_public_key=b"x"), signed)
    with pytest.raises(ProtocolViolation, match="timezone"):
        _verify(mapping, context, signer, signed, now=NOW.replace(tzinfo=None))


def test_duplicate_signed_json_keys_fail_closed(tmp_path):
    mapping, context, signer, private, stmt = _case(tmp_path)
    encoded = json.dumps(stmt, separators=(",", ":")).encode()
    duplicate = encoded.replace(b'{"_type":', b'{"_type":"evil","_type":', 1)
    assert duplicate != encoded
    signed = {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(duplicate).decode(),
        "signatures": [{
            "keyid": signer.keyid,
            "sig": base64.b64encode(private.sign(dsse_pae(duplicate))).decode(),
        }],
    }
    with pytest.raises(ProtocolViolation, match="duplicate signed"):
        _verify(mapping, context, signer, signed)
