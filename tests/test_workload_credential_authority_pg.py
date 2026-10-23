from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from dataclasses import replace

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_control_plane.workload_credentials import (
    ApprovalGrantRecord,
    CanonicalRunBinding,
    CredentialAuthorityDenied,
    CredentialRequestReplay,
    PinnedEd25519AssertionVerifier,
    PostgresCredentialAuthorityStore,
    WorkloadCredentialAuthority,
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _jwt(private_key, header, claims) -> str:
    first = _b64(json.dumps(header, separators=(",", ":")).encode())
    second = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{first}.{second}".encode("ascii")
    signature = _b64(private_key.sign(signing_input))
    return f"{first}.{second}.{signature}"


class FakeClock:
    def __init__(self, now=20_000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeRunAuthority:
    def __init__(self, binding):
        self.binding = binding

    def get_run_binding(self, run_id):
        if self.binding.run_id != run_id:
            raise LookupError("unknown run")
        return self.binding


@pytest.fixture
def pg_runtime():
    dsn = os.environ.get("ACP_WORKLOAD_CREDENTIAL_DATABASE_URL")
    if not dsn:
        pytest.skip("set ACP_WORKLOAD_CREDENTIAL_DATABASE_URL to run PostgreSQL integration tests")
    clock = FakeClock()
    suffix = uuid.uuid4().hex
    binding_id = f"pg-test-{suffix}"
    run = CanonicalRunBinding(
        tenant_id=f"tenant-{suffix}",
        agent_id=f"agent-{suffix}",
        release_id=f"release-{suffix}",
        session_id=f"session-{suffix}",
        run_id=f"run-{suffix}",
        sandbox_id=f"sandbox-{suffix}",
        sandbox_revision=1,
        workspace_ref=f"sandbox://kubernetes/sandbox-{suffix}",
        status="RUNNING",
        session_status="ACTIVE",
    )
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    verifier = PinnedEd25519AssertionVerifier(
        issuer="https://runtime.example.test",
        audience="urn:acp:workload-credential-authority",
        public_keys={"pg-test-key": public_key},
        max_lifetime_seconds=120,
    )
    store = PostgresCredentialAuthorityStore.from_dsn(dsn)
    binding = store.configure_binding(
        binding_id=binding_id,
        capability="github",
        audience="https://api.github.com",
        allowed_scopes=("github:read", "github:write"),
        max_ttl_seconds=90,
        active=True,
    )
    authority = WorkloadCredentialAuthority(
        store=store,
        assertion_verifier=verifier,
        run_authority=FakeRunAuthority(run),
        clock=clock,
    )

    def make_request(*, request_id, scopes=("github:read",), approval_id=None, ttl=60):
        claims = {
            "iss": "https://runtime.example.test",
            "aud": "urn:acp:workload-credential-authority",
            "iat": clock() - 1,
            "exp": clock() + 100,
            "sub": f"run:{run.run_id}",
            "workload_issuer": "runtime-prod",
            "tenant_id": run.tenant_id,
            "agent_id": run.agent_id,
            "release_id": run.release_id,
            "session_id": run.session_id,
            "run_id": run.run_id,
            "sandbox_id": run.sandbox_id,
            "sandbox_revision": run.sandbox_revision,
            "workspace_ref": run.workspace_ref,
            "binding_id": binding_id,
            "capability": "github",
            "audience": "https://api.github.com",
            "allowed_scopes": ["github:read", "github:write"],
            "scopes": list(scopes),
            "request_id": request_id,
            "requested_ttl_seconds": ttl,
            "approval_id": approval_id,
        }
        token = _jwt(
            private_key,
            {"alg": "EdDSA", "typ": "JWT", "kid": "pg-test-key"},
            claims,
        )
        return {
            "request_id": request_id,
            "binding_id": binding_id,
            "audience": "https://api.github.com",
            "scopes": list(scopes),
            "requested_ttl_seconds": ttl,
            "approval_id": approval_id,
            "workload_assertion": token,
        }

    yield clock, store, run, binding, authority, make_request

    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM public.acp_workload_credential_revocation_outbox WHERE approval_id IN (SELECT approval_id FROM public.acp_workload_approval_grants WHERE binding_id = %s)", (binding_id,))
        conn.execute("DELETE FROM public.acp_workload_approval_revocation_events WHERE approval_id IN (SELECT approval_id FROM public.acp_workload_approval_grants WHERE binding_id = %s)", (binding_id,))
        conn.execute("DELETE FROM public.acp_workload_credential_revocations WHERE resource_id IN (SELECT credential_id FROM public.acp_workload_credentials WHERE binding_id = %s) OR resource_id IN (SELECT run_id FROM public.acp_workload_credentials WHERE binding_id = %s) OR resource_id IN (SELECT approval_id FROM public.acp_workload_approval_grants WHERE binding_id = %s)", (binding_id, binding_id, binding_id))
        conn.execute("DELETE FROM public.acp_workload_credential_requests WHERE binding_id = %s", (binding_id,))
        conn.execute("DELETE FROM public.acp_workload_credentials WHERE binding_id = %s", (binding_id,))
        conn.execute("DELETE FROM public.acp_workload_approval_grants WHERE binding_id = %s", (binding_id,))
        conn.execute("DELETE FROM public.acp_workload_credential_bindings WHERE binding_id = %s", (binding_id,))


def test_postgres_persists_request_replay_and_current_run_fencing(pg_runtime):
    clock, store, run, binding, authority, request_factory = pg_runtime
    request = request_factory(request_id="pg-request-a")
    issued = authority.issue(request)["credential"]

    token_digest = hashlib.sha256(issued["access_token"].encode()).hexdigest()
    persisted = store.get_credential_by_digest(token_digest)
    assert persisted is not None
    assert persisted["binding_version"] == binding.version
    assert persisted["sandbox_revision"] == run.sandbox_revision
    assert "access_token" not in persisted

    active = authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": ["github:read"],
    })["credential"]
    assert active["active"] is True

    with pytest.raises(CredentialRequestReplay):
        authority.issue(request)

    inactive_reader = FakeRunAuthority(replace(run, sandbox_revision=2, sandbox_id="new-sandbox", workspace_ref="sandbox://kubernetes/new-sandbox"))
    authority._runs = inactive_reader
    assert authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": [],
    })["credential"]["active"] is False


def test_postgres_approval_revoke_is_transactional_idempotent_and_has_outbox(pg_runtime):
    clock, store, run, binding, authority, request_factory = pg_runtime
    approval_id = f"approval-{uuid.uuid4().hex}"
    grant = ApprovalGrantRecord(
        approval_id=approval_id,
        tenant_id=run.tenant_id,
        agent_id=run.agent_id,
        session_id=run.session_id,
        run_id=run.run_id,
        binding_id=binding.binding_id,
        audience=binding.audience,
        scopes=("github:write",),
        approved_by="human:security",
        issued_at=clock() - 1,
        expires_at=clock() + 30,
        state="ACTIVE",
        version=1,
    )
    store.register_approval(grant)
    request = request_factory(
        request_id="pg-request-write",
        scopes=("github:write",),
        approval_id=approval_id,
        ttl=60,
    )
    issued = authority.issue(request)["credential"]
    assert issued["expires_at"] <= grant.expires_at

    body = {
        "approval_id": approval_id,
        "event_id": f"approval-revoke-{uuid.uuid4().hex}",
        "_authenticated_service_role": "approval.revoker",
    }
    first = authority.revoke_approval_authority(approval_id, body)
    assert first["state"] == "REVOKED"
    second = authority.revoke_approval({
        "approval_id": approval_id,
        "event_id": body["event_id"],
        "_authenticated_service_role": "credential.revoker",
    })
    assert second["revoked_count"] == 1

    inactive = authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": ["github:write"],
    })["credential"]
    assert inactive["active"] is False

    with psycopg.connect(os.environ["ACP_WORKLOAD_CREDENTIAL_DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT event_id FROM public.acp_workload_credential_revocation_outbox WHERE event_id = %s",
            (body["event_id"],),
        ).fetchall()
    assert len(rows) == 1
