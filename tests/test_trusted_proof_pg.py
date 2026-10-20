"""Real PostgreSQL 16 acceptance for durable signed-proof references.

Separate disposable DB per test. No production credentials, external GitHub,
signer provisioning API, execution writes or public ingress.
"""
from __future__ import annotations

import copy
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("cryptography")
from psycopg import conninfo, sql  # noqa: E402

from agent_control_plane.state_transition_protocol import ProtocolViolation  # noqa: E402
from agent_control_plane.trusted_proof_pg import TrustedProofPostgres  # noqa: E402
from test_trusted_proof_envelope import _case, _signed  # noqa: E402


@pytest.fixture
def store():
    admin = os.environ.get("ACP_TRUSTED_PROOF_TEST_DSN")
    if not admin:
        pytest.skip("isolated ACP_TRUSTED_PROOF_TEST_DSN not configured")
    dbname = "acp_trust_" + uuid4().hex[:16]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname))
        )
    scoped = conninfo.make_conninfo(admin, dbname=dbname)
    try:
        client = TrustedProofPostgres(scoped)
        with pytest.raises(psycopg.errors.UndefinedTable):
            client.validate()  # no implicit request-time migrations
        client.install()
        client.install()  # operator idempotence
        client.validate()
        yield client
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(dbname)
                )
            )


def _signed_case(tmp_path):
    proof, context, signer, private, statement = _case(tmp_path)
    now = datetime.now(timezone.utc)
    statement["predicate"]["issued_at"] = (now - timedelta(seconds=10)).isoformat()
    statement["predicate"]["expires_at"] = (now + timedelta(seconds=120)).isoformat()
    return proof, context, signer, private, statement


def _provision(store, context, signer, *, enabled=True, revision=None):
    with store._connection() as conn:
        conn.execute(
            """INSERT INTO public.acp_trusted_proof_signers
               (tenant_id, project_id, keyid, public_key, policy_revision,
                max_age_seconds, enabled)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                context["tenant_id"], context["project_id"], signer.keyid,
                signer.ed25519_public_key,
                revision or context["policy_revision"],
                signer.max_age_seconds, enabled,
            ),
        )


def _count(store):
    with store._connection() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM public.acp_trusted_proof_receipts"
        ).fetchone()["n"]


def _verify(store, case):
    proof, context, signer, private, stmt = case
    return store.verify_and_record(
        proof_mapping=proof, expected_context=context,
        envelope=_signed(private, stmt),
    )


def test_real_pg16_identical_redelivery_is_durable_duplicate(tmp_path, store):
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    first = _verify(store, case)
    second = _verify(TrustedProofPostgres(store.dsn), case)
    assert first.status == "INSERTED"
    assert second.status == "DUPLICATE"
    assert first.proof_statement_hash == second.proof_statement_hash
    assert first.nonce == second.nonce
    assert _count(store) == 1
    with store._connection() as conn:
        row = conn.execute(
            "SELECT operation_id, nonce, signer_keyid "
            "FROM public.acp_trusted_proof_receipts"
        ).fetchone()
    assert row["operation_id"] == case[1]["operation_id"]
    assert row["nonce"] == case[1]["nonce"]
    assert row["signer_keyid"] == case[2].keyid


def test_concurrent_duplicate_claim_is_atomic(tmp_path, store):
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: _verify(store, case), range(8)))
    assert [r.status for r in outcomes].count("INSERTED") == 1
    assert [r.status for r in outcomes].count("DUPLICATE") == 7
    assert _count(store) == 1


def test_same_operation_different_nonce_conflicts(tmp_path, store):
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    _verify(store, case)
    proof, context, signer, private, stmt = case
    new_context = {**context, "nonce": "nonce-b"}
    new_stmt = copy.deepcopy(stmt)
    new_stmt["predicate"]["context"] = new_context
    with pytest.raises(ProtocolViolation, match="conflicting proof"):
        store.verify_and_record(
            proof_mapping=proof, envelope=_signed(private, new_stmt),
            expected_context=new_context,
        )
    assert _count(store) == 1


def test_same_nonce_other_cluster_or_operation_denied(tmp_path, store):
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    _verify(store, case)
    proof, context, signer, private, stmt = case
    other = {**context, "cluster_id": "another-cluster"}
    other_stmt = copy.deepcopy(stmt)
    other_stmt["predicate"]["context"] = other
    with pytest.raises(ProtocolViolation, match="nonce already bound"):
        store.verify_and_record(
            proof_mapping=proof, envelope=_signed(private, other_stmt),
            expected_context=other,
        )
    assert _count(store) == 1


def test_revoked_signer_denies_even_identical_redelivery(tmp_path, store):
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    _verify(store, case)
    with store._connection() as conn:
        conn.execute(
            "UPDATE public.acp_trusted_proof_signers SET enabled=FALSE"
        )
    with pytest.raises(ProtocolViolation, match="revoked"):
        _verify(store, case)
    assert _count(store) == 1


def test_disabled_and_missing_signer_are_denied(tmp_path, store):
    case = _signed_case(tmp_path)
    with pytest.raises(ProtocolViolation, match="missing or revoked"):
        _verify(store, case)
    _provision(store, case[1], case[2], enabled=False)
    with pytest.raises(ProtocolViolation, match="missing or revoked"):
        _verify(store, case)
    assert _count(store) == 0


def test_policy_revision_mismatch_denied(tmp_path, store):
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2], revision="new-policy-v8")
    with pytest.raises(ProtocolViolation, match="stale"):
        _verify(store, case)
    assert _count(store) == 0


def test_wrong_public_key_denied_before_claim(tmp_path, store):
    case = _signed_case(tmp_path)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    fake = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    with store._connection() as conn:
        conn.execute(
            """INSERT INTO public.acp_trusted_proof_signers
               (tenant_id, project_id, keyid, public_key, policy_revision,
                max_age_seconds, enabled)
               VALUES (%s,%s,%s,%s,%s,%s,TRUE)""",
            (
                case[1]["tenant_id"], case[1]["project_id"], case[2].keyid,
                fake, case[1]["policy_revision"], 600,
            ),
        )
    with pytest.raises(ProtocolViolation, match="signature"):
        _verify(store, case)
    assert _count(store) == 0


def test_expired_signed_reference_denied_using_database_clock(tmp_path, store):
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    case[4]["predicate"]["issued_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=3)
    ).isoformat()
    case[4]["predicate"]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=2)
    ).isoformat()
    with pytest.raises(ProtocolViolation, match="expired"):
        _verify(store, case)
    assert _count(store) == 0


def test_signed_reference_requires_exact_original_proof(tmp_path, store):
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    proof = copy.deepcopy(case[0])
    proof["proof_id"] = "tampered-proof"
    with pytest.raises(ProtocolViolation):
        store.verify_and_record(
            proof_mapping=proof, expected_context=case[1],
            envelope=_signed(case[3], case[4]),
        )
    assert _count(store) == 0


def test_no_implicit_repair_on_pinned_schema_checksum_drift(store):
    with store._connection() as conn:
        conn.execute(
            "UPDATE public.acp_trusted_proof_schema SET checksum=%s",
            ("f" * 64,),
        )
    with pytest.raises(ProtocolViolation, match="checksum"):
        store.validate()
    with pytest.raises(ProtocolViolation, match="checksum"):
        store.install()


def test_signature_rejected_after_policy_rotation_even_if_receipt_exists(
    tmp_path, store,
):
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    _verify(store, case)
    with store._connection() as conn:
        conn.execute(
            "UPDATE public.acp_trusted_proof_signers "
            "SET policy_revision='policy-v8'"
        )
    with pytest.raises(ProtocolViolation, match="stale"):
        _verify(store, case)
    assert _count(store) == 1
