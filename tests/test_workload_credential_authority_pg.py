from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from dataclasses import replace

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_control_plane.workload_credentials import (
    ApprovalGrantRecord,
    CanonicalRunBinding,
    CredentialAuthorityConflict,
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
        approval_hash="sha256:" + "a" * 64,
        action_hash="sha256:" + "b" * 64,
        transition_hash="sha256:" + "c" * 64,
        policy_decision_hash="sha256:" + "d" * 64,
    )
    store._insert_verified_approval(grant)
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


def test_postgres_concurrent_duplicate_issue_persists_exactly_one_credential(pg_runtime):
    clock, store, run, binding, authority, request_factory = pg_runtime
    request_id = f"pg-concurrent-{uuid.uuid4().hex}"
    request = request_factory(request_id=request_id)
    barrier = Barrier(2)

    def issue_after_barrier():
        barrier.wait(timeout=10)
        try:
            return ("issued", authority.issue(request)["credential"])
        except CredentialRequestReplay:
            return ("replay", None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(issue_after_barrier) for _ in range(2)]
        outcomes = [future.result(timeout=20) for future in futures]

    assert sorted(kind for kind, _ in outcomes) == ["issued", "replay"]
    issued = next(value for kind, value in outcomes if kind == "issued")
    assert isinstance(issued["credential_id"], str) and issued["credential_id"]

    with psycopg.connect(os.environ["ACP_WORKLOAD_CREDENTIAL_DATABASE_URL"]) as conn:
        request_rows = conn.execute(
            """
            SELECT count(*) AS n
            FROM public.acp_workload_credential_requests
            WHERE issuer = %s AND run_id = %s
              AND binding_id = %s AND request_id = %s
            """,
            ("runtime-prod", run.run_id, binding.binding_id, request_id),
        ).fetchone()[0]
        credential_rows = conn.execute(
            """
            SELECT count(*) AS n
            FROM public.acp_workload_credentials
            WHERE workload_issuer = %s AND run_id = %s
              AND binding_id = %s
              AND credential_id IN (
                  SELECT credential_id
                  FROM public.acp_workload_credential_requests
                  WHERE issuer = %s AND run_id = %s
                    AND binding_id = %s AND request_id = %s
              )
            """,
            (
                "runtime-prod", run.run_id, binding.binding_id,
                "runtime-prod", run.run_id, binding.binding_id, request_id,
            ),
        ).fetchone()[0]

    assert request_rows == 1
    assert credential_rows == 1


def test_postgres_lost_issue_response_reconciles_without_returning_raw_token(pg_runtime):
    clock, store, run, binding, authority, request_factory = pg_runtime
    request_id = f"pg-lost-ack-{uuid.uuid4().hex}"
    request = request_factory(request_id=request_id)

    def issue_then_drop_response():
        # The service commits the credential; the caller loses the response.
        authority.issue(request)
        raise ConnectionError("injected response loss after successful issuance")

    with pytest.raises(ConnectionError, match="injected response loss"):
        issue_then_drop_response()

    recovered = store.lookup_issue_request(
        issuer="runtime-prod",
        run_id=run.run_id,
        binding_id=binding.binding_id,
        request_id=request_id,
        now=clock(),
    )
    assert recovered is not None
    assert recovered["state"] == "ISSUED"
    assert recovered["request_id"] == request_id
    assert recovered["run_id"] == run.run_id
    assert "access_token" not in recovered
    assert "token_digest" not in recovered

    # Retrying the mutating request is denied; reconciliation must be read-only.
    with pytest.raises(CredentialRequestReplay):
        authority.issue(request)

    with psycopg.connect(os.environ["ACP_WORKLOAD_CREDENTIAL_DATABASE_URL"]) as conn:
        count = conn.execute(
            """
            SELECT count(*) AS n
            FROM public.acp_workload_credential_requests
            WHERE issuer = %s AND run_id = %s
              AND binding_id = %s AND request_id = %s
            """,
            ("runtime-prod", run.run_id, binding.binding_id, request_id),
        ).fetchone()[0]
    assert count == 1


def test_postgres_revocation_event_id_cannot_be_rebound_to_another_resource(pg_runtime):
    clock, store, run, binding, authority, request_factory = pg_runtime
    first = authority.issue(request_factory(
        request_id=f"pg-revoke-a-{uuid.uuid4().hex}"
    ))["credential"]
    second = authority.issue(request_factory(
        request_id=f"pg-revoke-b-{uuid.uuid4().hex}"
    ))["credential"]
    event_id = f"pg-revoke-event-{uuid.uuid4().hex}"

    assert store.revoke_credential(
        first["credential_id"],
        event_id=event_id,
        actor_role="credential.revoker",
        now=clock(),
    ) is True

    with pytest.raises(CredentialAuthorityConflict):
        store.revoke_credential(
            second["credential_id"],
            event_id=event_id,
            actor_role="credential.revoker",
            now=clock(),
        )

    with pytest.raises(CredentialAuthorityConflict):
        store.revoke_run(
            run.run_id,
            sandbox_revision=run.sandbox_revision,
            event_id=event_id,
            actor_role="credential.revoker",
            now=clock(),
        )

    approval_id = f"pg-rebound-approval-{uuid.uuid4().hex}"
    store._insert_verified_approval(ApprovalGrantRecord(
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
        expires_at=clock() + 60,
        state="ACTIVE",
        version=1,
        approval_hash="sha256:" + "e" * 64,
        action_hash="sha256:" + "f" * 64,
        transition_hash="sha256:" + "1" * 64,
        policy_decision_hash="sha256:" + "2" * 64,
    ))
    with pytest.raises(CredentialAuthorityConflict):
        store.revoke_approval(
            approval_id,
            event_id=event_id,
            actor_role="credential.revoker",
            now=clock(),
        )


def test_postgres_outbox_reclaim_after_lost_ack_reuses_stable_event_id(pg_runtime):
    clock, store, run, binding, authority, request_factory = pg_runtime
    approval_id = f"pg-outbox-{uuid.uuid4().hex}"
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
        expires_at=clock() + 60,
        state="ACTIVE",
        version=1,
        approval_hash="sha256:" + "a" * 64,
        action_hash="sha256:" + "b" * 64,
        transition_hash="sha256:" + "c" * 64,
        policy_decision_hash="sha256:" + "d" * 64,
    )
    store._insert_verified_approval(grant)
    issued = authority.issue(request_factory(
        request_id=f"pg-outbox-request-{uuid.uuid4().hex}",
        scopes=("github:write",),
        approval_id=approval_id,
        ttl=60,
    ))["credential"]
    event_id = f"pg-outbox-event-{uuid.uuid4().hex}"
    authority.revoke_approval_authority(
        approval_id,
        {
            "event_id": event_id,
            "_authenticated_service_role": "approval.revoker",
        },
    )

    first_claim = store.claim_revocation_outbox(
        claim_id=f"claim-{uuid.uuid4().hex}",
        now=clock(),
        lease_seconds=5,
        limit=10,
    )
    first = next(item for item in first_claim if item["event_id"] == event_id)
    published_payload = dict(first["payload"])

    # A downstream consumer deduplicates by stable event ID; the side effect
    # succeeds on first delivery even though the producer loses its ACK.
    receiver_seen: set[str] = set()
    receiver_effects: list[dict[str, object]] = []

    def receiver_accept(payload):
        stable_id = payload["event_id"]
        if stable_id in receiver_seen:
            return
        receiver_seen.add(stable_id)
        receiver_effects.append(dict(payload))

    receiver_accept(published_payload)
    # Simulate publish succeeding, then the worker dies before persisting ACK.
    clock.now += 6
    second_claim_id = f"claim-{uuid.uuid4().hex}"
    second_claim = store.claim_revocation_outbox(
        claim_id=second_claim_id,
        now=clock(),
        lease_seconds=5,
        limit=10,
    )
    replayed = next(item for item in second_claim if item["event_id"] == event_id)

    assert replayed["event_id"] == event_id
    assert dict(replayed["payload"]) == published_payload
    assert replayed["attempts"] == first["attempts"] + 1
    receiver_accept(dict(replayed["payload"]))
    assert len(receiver_effects) == 1
    assert receiver_effects[0]["event_id"] == event_id
    assert store.acknowledge_revocation_outbox(
        event_id=event_id,
        claim_id=second_claim_id,
        delivered_at=clock(),
    ) is True

    # The receiver-side exercise is deterministic; a real network publisher
    # and independently deployed receiver remain a separate acceptance gate.
    assert authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": ["github:write"],
    })["credential"]["active"] is False
