"""Real PG16 low-privilege runtime tests for ACP trusted proof signer locks."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("cryptography")

from psycopg import conninfo, sql  # noqa: E402

from agent_control_plane.state_transition_protocol import ProtocolViolation  # noqa: E402
from agent_control_plane.trusted_proof_pg import TrustedProofPostgres  # noqa: E402
from agent_control_plane.trusted_proof_pg_security import (  # noqa: E402
    FUNCTION_SIGNATURE,
)
from test_trusted_proof_pg import (  # noqa: E402
    _provision,
    _signed_case,
    store as store,
)


@pytest.fixture
def verifier_role(store):
    """Create a real LOGIN role with only the reviewed runtime GRANTs."""
    admin_dsn = os.environ["ACP_TRUSTED_PROOF_TEST_DSN"]
    role = "acp_verifier_" + uuid4().hex[:14]
    password = uuid4().hex + uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password),
            )
        )
    try:
        store.grant_runtime(role)
        restricted_dsn = conninfo.make_conninfo(
            store.dsn, user=role, password=password,
        )
        yield TrustedProofPostgres(restricted_dsn), role
    finally:
        # Drop privileges in the disposable test database before DROP ROLE.
        with store._connection() as conn:
            conn.execute(
                sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role))
            )
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_restricted_role_verifies_and_deduplicates_without_table_update(
    tmp_path, store, verifier_role,
):
    restricted, role = verifier_role
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    args = {
        "proof_mapping": case[0],
        "expected_context": case[1],
    }
    from test_trusted_proof_envelope import _signed
    args["envelope"] = _signed(case[3], case[4])
    assert restricted.verify_and_record(**args).status == "INSERTED"
    assert restricted.verify_and_record(**args).status == "DUPLICATE"
    with store._connection() as operator:
        privileges = operator.execute(
            """SELECT
                has_table_privilege(%s,
                    'public.acp_trusted_proof_signers','UPDATE') AS signer_update,
                has_table_privilege(%s,
                    'public.acp_trusted_proof_receipts','UPDATE') AS receipt_update,
                has_function_privilege(%s, %s, 'EXECUTE') AS can_execute""",
            (role, role, role, FUNCTION_SIGNATURE),
        ).fetchone()
    assert privileges == {
        "signer_update": False,
        "receipt_update": False,
        "can_execute": True,
    }


@pytest.mark.parametrize("statement", [
    "SELECT * FROM public.acp_trusted_proof_signers",
    "UPDATE public.acp_trusted_proof_signers SET enabled=TRUE",
    "DELETE FROM public.acp_trusted_proof_signers",
    "INSERT INTO public.acp_trusted_proof_signers "
    "(tenant_id,project_id,keyid,public_key,policy_revision,max_age_seconds) "
    "VALUES ('t','p','k',decode(repeat('ab',32),'hex'),'v1',10)",
    "UPDATE public.acp_trusted_proof_receipts SET nonce='forged'",
    "DELETE FROM public.acp_trusted_proof_receipts",
    "CREATE TABLE public.acp_extraneous (id text)",
])
def test_restricted_runtime_cannot_administer_or_tamper(
    verifier_role, statement,
):
    restricted, _ = verifier_role
    with restricted._connection() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


def test_revoked_signer_fails_for_restricted_runtime_even_on_replay(
    tmp_path, store, verifier_role,
):
    restricted, _ = verifier_role
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    from test_trusted_proof_envelope import _signed
    args = {
        "proof_mapping": case[0], "expected_context": case[1],
        "envelope": _signed(case[3], case[4]),
    }
    restricted.verify_and_record(**args)
    with store._connection() as conn:
        conn.execute(
            "UPDATE public.acp_trusted_proof_signers SET enabled=FALSE"
        )
    with pytest.raises(ProtocolViolation, match="revoked"):
        restricted.verify_and_record(**args)


def test_committed_revocation_wins_over_waiting_verifier(
    tmp_path, store, verifier_role,
):
    restricted, _ = verifier_role
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    from test_trusted_proof_envelope import _signed
    args = {
        "proof_mapping": case[0], "expected_context": case[1],
        "envelope": _signed(case[3], case[4]),
    }
    with store._connection() as admin:
        admin.execute("UPDATE public.acp_trusted_proof_signers SET enabled=FALSE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(restricted.verify_and_record, **args)
            # Do not assert scheduling order; the signer lock must serialize
            # with this transaction's outstanding no-key UPDATE.
            admin.commit()
            with pytest.raises(ProtocolViolation, match="revoked"):
                waiting.result(timeout=10)


def test_public_execute_grant_is_detected_as_drift(store):
    with store._connection() as conn:
        conn.execute(
            "GRANT EXECUTE ON FUNCTION "
            + FUNCTION_SIGNATURE + " TO PUBLIC"
        )
    with pytest.raises(ProtocolViolation, match="function security drift"):
        store.validate()


def test_security_invoker_change_is_detected_as_drift(store):
    with store._connection() as conn:
        conn.execute(
            "ALTER FUNCTION " + FUNCTION_SIGNATURE + " SECURITY INVOKER"
        )
    with pytest.raises(ProtocolViolation, match="function security drift"):
        store.validate()


def test_extension_checksum_drift_fails_closed(store):
    with store._connection() as conn:
        conn.execute(
            "UPDATE public.acp_trusted_proof_security_schema "
            "SET checksum=%s", ("f" * 64,),
        )
    with pytest.raises(ProtocolViolation, match="security ledger"):
        store.validate()


def test_runtime_cannot_reinstall_or_grant_permissions(verifier_role):
    restricted, role = verifier_role
    with pytest.raises(ProtocolViolation, match="schema owner"):
        restricted.install()
    with pytest.raises(ProtocolViolation, match="schema owner"):
        restricted.grant_runtime(role)


def test_restricted_role_fails_if_policy_changes(tmp_path, store, verifier_role):
    restricted, _ = verifier_role
    case = _signed_case(tmp_path)
    _provision(store, case[1], case[2])
    from test_trusted_proof_envelope import _signed
    with store._connection() as conn:
        conn.execute(
            "UPDATE public.acp_trusted_proof_signers "
            "SET policy_revision='future-revision'"
        )
    with pytest.raises(ProtocolViolation, match="stale"):
        restricted.verify_and_record(
            proof_mapping=case[0], expected_context=case[1],
            envelope=_signed(case[3], case[4]),
        )


@pytest.mark.parametrize("privilege", ["DELETE", "TRUNCATE"])
def test_operator_refuses_roles_with_preexisting_signer_write_privileges(
    store, verifier_role, privilege,
):
    _, role = verifier_role
    with store._connection() as conn:
        conn.execute(
            sql.SQL("GRANT " + privilege + " ON "
                    "public.acp_trusted_proof_signers TO {}").format(
                sql.Identifier(role)
            )
        )
    with pytest.raises(ProtocolViolation, match="exceed least-privilege"):
        store.grant_runtime(role)
