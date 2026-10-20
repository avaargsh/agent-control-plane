"""Real PostgreSQL 16 runtime-scoped current authority and held-write tests."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import conninfo, sql  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from agent_control_plane.execution_journal import ExecutionAttemptState  # noqa: E402
from agent_control_plane.live_effect_boundary import (  # noqa: E402
    LiveEffectAdmissionDenied,
    LiveEffectBoundaryGate,
)
from agent_control_plane.live_effect_pg import (  # noqa: E402
    PostgresCurrentEffectAuthorityReader,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation  # noqa: E402
from test_live_effect_boundary import (  # noqa: E402
    CHECK_AT,
    SCOPE,
    _scenario,
)
from test_execution_lifecycle import _execute  # noqa: E402


@pytest.fixture
def authority_db():
    root = os.environ.get("ACP_LIVE_EFFECT_TEST_DSN")
    if not root:
        pytest.skip("isolated ACP_LIVE_EFFECT_TEST_DSN not configured")
    dbname = "acp_live_" + uuid4().hex[:16]
    with psycopg.connect(root, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    dsn = conninfo.make_conninfo(root, dbname=dbname)
    try:
        operator = PostgresCurrentEffectAuthorityReader(dsn)
        with pytest.raises(psycopg.errors.UndefinedTable):
            operator.validate()
        operator.install()
        operator.install()
        operator.validate()
        yield operator, root
    finally:
        with psycopg.connect(root, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname))
            )


@pytest.fixture
def restricted_reader(authority_db):
    operator, root = authority_db
    role = "live_effect_" + uuid4().hex[:14]
    password = uuid4().hex + uuid4().hex
    with psycopg.connect(root, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password),
            )
        )
    try:
        operator.grant_reader(
            role=role,tenant_id=SCOPE.tenant_id,
            project_id=SCOPE.project_id,cluster_id=SCOPE.cluster_id,
        )
        scoped = conninfo.make_conninfo(operator.dsn,user=role,password=password)
        reader=PostgresCurrentEffectAuthorityReader(scoped)
        reader.validate()
        yield operator, reader, role
    finally:
        with operator._connection() as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        with psycopg.connect(root, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def _payload(snapshot):
    return {
        "nonce": snapshot.nonce, "resource_uid": snapshot.resource_uid,
        "principal_type": snapshot.principal.type,
        "principal_subject": snapshot.principal.subject,
        "executor_type": snapshot.executor.type,
        "executor_subject": snapshot.executor.subject,
        "resource_generation": snapshot.resource_generation,
        "lease_id": snapshot.lease_id,"lease_epoch": snapshot.lease_epoch,
        "transition_hash": snapshot.transition_hash,
        "action_hash": snapshot.action_hash,
        "authorization_hash": snapshot.authorization_hash,
        "approval_hash": snapshot.approval_hash,
        "policy_version": snapshot.policy_version,
        "policy_input_hash": snapshot.policy_input_hash,
        "policy_decision_hash": snapshot.policy_decision_hash,
        "policy_effect": snapshot.policy_effect,
        "approval_state": snapshot.approval_state,
        "principal_state": snapshot.principal_state,
        "operation_state": snapshot.operation_state,
    }


def _seed(operator, snapshot):
    with operator._connection() as conn:
        conn.execute(
            """INSERT INTO public.acp_live_effect_authority (
                 tenant_id,project_id,cluster_id,work_id,operation_id,
                 nonce,version,observed_at,valid_until,snapshot
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (snapshot.tenant_id,snapshot.project_id,snapshot.cluster_id,
             snapshot.work_id,snapshot.operation_id,snapshot.nonce,
             snapshot.version,snapshot.observed_at,snapshot.valid_until,
             Jsonb(_payload(snapshot))),
        )


def _modify(operator, *, snapshot, **changes):
    payload = {**_payload(snapshot), **changes}
    with operator._connection() as conn:
        conn.execute(
            """UPDATE public.acp_live_effect_authority
               SET snapshot=%s,version=version+1
               WHERE tenant_id=%s AND project_id=%s AND cluster_id=%s
                 AND work_id=%s AND operation_id=%s""",
            (Jsonb(payload),snapshot.tenant_id,snapshot.project_id,
             snapshot.cluster_id,snapshot.work_id,snapshot.operation_id),
        )


def _with_real_reader(tmp_path, restricted_reader):
    operator, reader, role = restricted_reader
    scene=list(_scenario(tmp_path))
    original = scene[3]
    _seed(operator,scene[-1].snapshot)
    scene[3] = replace(original, live_effect_gate=LiveEffectBoundaryGate(
        scope=SCOPE,reader=reader,clock=lambda: CHECK_AT,
    ))
    return tuple(scene)


def test_pg16_external_authority_drives_real_effect_path(
    tmp_path,restricted_reader,
):
    scene=_with_real_reader(tmp_path,restricted_reader)
    fixture,_,_,context,coordinator,journal,_,prepared,_=scene
    accepted=context.live_effect_gate.reader.read_current(
        tenant_id=SCOPE.tenant_id,project_id=SCOPE.project_id,
        cluster_id=SCOPE.cluster_id,work_id=context.proposal.work_id,
        operation_id=prepared.attempt.operation_id,
    )
    assert accepted == scene[-1].snapshot
    result=_execute(coordinator,fixture,context)
    assert result.attempt.state is ExecutionAttemptState.COMMITTED
    assert fixture["api"].patch_calls == 1
    assert journal.get(result.attempt.attempt_id)==result.attempt


@pytest.mark.parametrize(("field","value"),[
    ("policy_effect","DENY"),
    ("approval_state","REVOKED"),
    ("principal_state","DISABLED"),
    ("operation_state","CANCELLED"),
    ("nonce","different-operation-nonce"),
    ("authorization_hash","swapped-authorization"),
])
def test_pg16_operator_revocation_before_effect_denies_patch(
    tmp_path,restricted_reader,field,value,
):
    scene=_with_real_reader(tmp_path,restricted_reader)
    operator=restricted_reader[0]
    _modify(operator,snapshot=scene[-1].snapshot,**{field:value})
    fixture,_,_,context,coordinator,journal,_,prepared,_=scene
    with pytest.raises(LiveEffectAdmissionDenied):
        _execute(coordinator,fixture,context)
    assert fixture["api"].patch_calls==0
    assert journal.get(prepared.attempt.attempt_id).state is ExecutionAttemptState.ABORTED
    assert journal.get(prepared.attempt.attempt_id).result["status"]=="NOT_APPLIED"


@pytest.mark.parametrize("statement",[
    "SELECT * FROM public.acp_live_effect_authority",
    "UPDATE public.acp_live_effect_authority SET version=999",
    "DELETE FROM public.acp_live_effect_authority",
    "TRUNCATE public.acp_live_effect_authority",
    "SELECT * FROM public.acp_live_effect_readers",
    "UPDATE public.acp_live_effect_readers SET project_id='attacker'",
    "CREATE TABLE public.acp_fake_authority (id int)",
])
def test_restricted_reader_cannot_administrate_current_authority(
    restricted_reader,statement,
):
    reader=restricted_reader[1]
    with reader._connection() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


def test_pg16_wrong_tenant_or_project_role_fails_closed(
    tmp_path,restricted_reader,
):
    scene=_with_real_reader(tmp_path,restricted_reader)
    reader=restricted_reader[1]
    snapshot=scene[-1].snapshot
    assert reader.read_current(
        tenant_id="other",project_id=SCOPE.project_id,
        cluster_id=SCOPE.cluster_id,work_id=snapshot.work_id,
        operation_id=snapshot.operation_id,
    ) is None
    assert reader.read_current(
        tenant_id=SCOPE.tenant_id,project_id="other-project",
        cluster_id=SCOPE.cluster_id,work_id=snapshot.work_id,
        operation_id=snapshot.operation_id,
    ) is None
    with pytest.raises(ProtocolViolation,match="silently change scope"):
        restricted_reader[0].grant_reader(
            role=restricted_reader[2],tenant_id=SCOPE.tenant_id,
            project_id="other-project",cluster_id=SCOPE.cluster_id,
        )


def test_pg16_scope_grant_is_not_public(tmp_path,restricted_reader):
    scene=_with_real_reader(tmp_path,restricted_reader)
    reader=restricted_reader[1]
    with reader._connection() as conn:
        public=conn.execute(
            """SELECT EXISTS (
               SELECT 1 FROM aclexplode(coalesce(
                 p.proacl,acldefault('f',p.proowner))) acl
               WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
            ) AS granted FROM pg_proc p
            WHERE p.oid=to_regprocedure(
              'public.acp_live_effect_read(text,text,text,text,text)')"""
        ).fetchone()
    assert public["granted"] is False
    assert scene[3].live_effect_gate.reader is reader


def test_policy_revocation_issued_during_patch_waits_for_locked_receipt(
    tmp_path,restricted_reader,
):
    scene=_with_real_reader(tmp_path,restricted_reader)
    fixture,_,_,context,coordinator,journal,_,prepared,_=scene
    operator=restricted_reader[0]
    entered=Event()
    release=Event()
    revocation_started=Event()
    revocation_finished=Event()
    original_patch=fixture["api"].patch_deployment

    def slow_patch(*,namespace,name,patch):
        entered.set()
        if not release.wait(timeout=8):
            raise RuntimeError("test-only patch barrier exceeded")
        return original_patch(namespace=namespace,name=name,patch=patch)

    fixture["api"].patch_deployment=slow_patch

    def revoke():
        with operator._connection() as conn:
            conn.execute("SET LOCAL lock_timeout = '8s'")
            revocation_started.set()
            conn.execute(
                """UPDATE public.acp_live_effect_authority SET
                     snapshot=jsonb_set(snapshot,'{approval_state}',
                       '"REVOKED"'::jsonb),
                     version=version+1
                   WHERE tenant_id=%s AND project_id=%s AND cluster_id=%s
                     AND work_id=%s AND operation_id=%s""",
                (SCOPE.tenant_id,SCOPE.project_id,SCOPE.cluster_id,
                 scene[-1].snapshot.work_id,prepared.attempt.operation_id),
            )
        revocation_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        running=pool.submit(_execute,coordinator,fixture,context)
        assert entered.wait(timeout=10),"provider PATCH boundary not reached"
        revoking=pool.submit(revoke)
        try:
            assert revocation_started.wait(timeout=10)
            assert not revocation_finished.wait(timeout=0.2), (
                "revocation overtook an in-progress guarded PATCH"
            )
        finally:
            release.set()
        result=running.result(timeout=12)
        revoking.result(timeout=12)
    assert result.attempt.state is ExecutionAttemptState.COMMITTED
    assert fixture["api"].patch_calls==1
    after=context.live_effect_gate.reader.read_current(
        tenant_id=SCOPE.tenant_id,project_id=SCOPE.project_id,
        cluster_id=SCOPE.cluster_id,work_id=context.proposal.work_id,
        operation_id=prepared.attempt.operation_id,
    )
    assert after.approval_state=="REVOKED"
    assert journal.get(result.attempt.attempt_id).state is ExecutionAttemptState.COMMITTED


def test_after_patch_database_ack_uncertainty_is_not_not_applied(
    tmp_path,restricted_reader,
):
    scene=_with_real_reader(tmp_path,restricted_reader)
    fixture,_,_,context,coordinator,journal,_,prepared,_=scene
    real_reader=restricted_reader[1]

    class AcknowledgementLostReader:
        @contextmanager
        def hold_current(self, **kwargs):
            with real_reader.hold_current(**kwargs) as snapshot:
                yield snapshot
            raise RuntimeError("simulated DB commit acknowledgement lost")

    broken=replace(context,live_effect_gate=replace(
        context.live_effect_gate,reader=AcknowledgementLostReader(),
    ))
    with pytest.raises(RuntimeError,match="commit acknowledgement lost"):
        _execute(coordinator,fixture,broken)
    assert fixture["api"].patch_calls==1
    assert journal.get(prepared.attempt.attempt_id).state is ExecutionAttemptState.PREPARED


@pytest.mark.parametrize("mutation",["PUBLIC EXECUTE","SECURITY INVOKER"])
def test_pg16_function_security_drift_fails_before_patch(
    tmp_path,restricted_reader,mutation,
):
    scene=_with_real_reader(tmp_path,restricted_reader)
    operator=restricted_reader[0]
    with operator._connection() as conn:
        if mutation=="PUBLIC EXECUTE":
            conn.execute(
                "GRANT EXECUTE ON FUNCTION "
                "public.acp_live_effect_read(text,text,text,text,text) TO PUBLIC"
            )
        else:
            conn.execute(
                "ALTER FUNCTION public.acp_live_effect_read(text,text,text,text,text) "
                "SECURITY INVOKER"
            )
    with pytest.raises(LiveEffectAdmissionDenied):
        _execute(scene[4],scene[0],scene[3])
    assert scene[0]["api"].patch_calls==0


def test_runtime_cannot_install_schema_or_grant_role(restricted_reader):
    _,reader,role=restricted_reader
    with pytest.raises(ProtocolViolation,match="ownership"):
        reader.install()
    with pytest.raises(ProtocolViolation,match="ownership"):
        reader.grant_reader(
            role=role,tenant_id=SCOPE.tenant_id,
            project_id=SCOPE.project_id,cluster_id=SCOPE.cluster_id,
        )
