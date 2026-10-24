"""Cross-repository RuntimeStore -> ACP credential authority integration.

Runs only where the reviewed cloud-agent-runtime checkout and PostgreSQL 16
authority schema are installed (the dedicated workload-credential-authority-pg
workflow). Assertions are signed with a test-only key trusted by this test's
verifier; mismatches therefore exercise the canonical-state boundary, not a
signature-verification shortcut.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time
import uuid

import pytest

# Generic ACP unit-test jobs do not install the Runtime checkout. The dedicated
# PostgreSQL integration job sets the DSN and must fail closed if its pinned
# Runtime commit lacks the canonical reader or cryptography dependency; never
# convert a broken cross-repo integration into a green importorskip.
if not os.environ.get("ACP_WORKLOAD_CREDENTIAL_DATABASE_URL"):
    pytest.skip(
        "dedicated PostgreSQL authority workflow is required",
        allow_module_level=True,
    )

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cloud_agent_runtime.canonical_runtime_identity import RuntimeStoreRunAuthorityReader
from cloud_agent_runtime.models import (
    Run,
    RunStatus,
    SandboxBinding,
    Session,
    SessionStatus,
)
from cloud_agent_runtime.store import SQLiteStore

from agent_control_plane.workload_credentials import (
    CredentialAuthorityDenied,
    PinnedEd25519AssertionVerifier,
    PostgresCredentialAuthorityStore,
    WorkloadCredentialAuthority,
)


ISSUER = "https://runtime.example.test"
ASSERTION_AUDIENCE = "urn:acp:workload-credential-authority"
RESOURCE_AUDIENCE = "https://api.github.com"
KEY_ID = "cross-repo-test-key"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _sign_assertion(private_key: Ed25519PrivateKey, claims: dict[str, object]) -> str:
    header = {"alg": "EdDSA", "typ": "JWT", "kid": KEY_ID}
    first = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    second = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{first}.{second}".encode("ascii")
    signature = _b64url(private_key.sign(signing_input))
    return f"{first}.{second}.{signature}"


@pytest.fixture
def integration_context(tmp_path):
    dsn = os.environ.get("ACP_WORKLOAD_CREDENTIAL_DATABASE_URL")
    if not dsn:
        pytest.skip("dedicated PostgreSQL authority workflow is required")

    now = time.time()
    suffix = uuid.uuid4().hex
    session_id = f"cross-session-{suffix}"
    run_id = f"cross-run-{suffix}"
    tenant_id = f"cross-tenant-{suffix}"
    agent_id = f"cross-agent-{suffix}"
    release_id = f"cross-release-{suffix}"
    sandbox_id = f"cross-sandbox-{suffix}"
    workspace_ref = f"sandbox://kubernetes/{sandbox_id}"
    binding_id = f"cross-binding-{suffix}"

    runtime_store_path = tmp_path / f"runtime-authority-{suffix}.sqlite"
    runtime_store = SQLiteStore(runtime_store_path)
    runtime_store.save_session(Session(
        session_id=session_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        release_id=release_id,
        status=SessionStatus.ACTIVE,
    ))
    runtime_store.save_run(Run(
        run_id=run_id,
        session_id=session_id,
        status=RunStatus.RUNNING,
        sandbox_ref=workspace_ref,
        sandbox_binding=SandboxBinding(
            provider="kubernetes",
            sandbox_id=sandbox_id,
            sandbox_ref=workspace_ref,
            revision=7,
        ),
    ))
    run_reader = RuntimeStoreRunAuthorityReader(runtime_store)

    credential_store = PostgresCredentialAuthorityStore.from_dsn(dsn)
    credential_store.configure_binding(
        binding_id=binding_id,
        capability="github",
        audience=RESOURCE_AUDIENCE,
        allowed_scopes=["github:read"],
        max_ttl_seconds=60,
    )

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    verifier = PinnedEd25519AssertionVerifier(
        issuer=ISSUER,
        audience=ASSERTION_AUDIENCE,
        public_keys={KEY_ID: public_key},
        max_lifetime_seconds=60,
    )
    authority = WorkloadCredentialAuthority(
        store=credential_store,
        assertion_verifier=verifier,
        run_authority=run_reader,
        clock=lambda: now,
        token_factory=lambda: "cross-repo-test-token-" + secrets.token_urlsafe(32),
    )
    context = {}

    def reopen_runtime_authority():
        """Simulate Runtime and the authority reader losing their process state."""
        nonlocal runtime_store, run_reader, authority
        runtime_store.close()
        runtime_store = SQLiteStore(runtime_store_path)
        run_reader = RuntimeStoreRunAuthorityReader(runtime_store)
        authority = WorkloadCredentialAuthority(
            store=credential_store,
            assertion_verifier=verifier,
            run_authority=run_reader,
            clock=lambda: now,
            token_factory=lambda: "cross-repo-test-token-" + secrets.token_urlsafe(32),
        )
        context.update(
            runtime_store=runtime_store,
            run_reader=run_reader,
            authority=authority,
        )

    def request(*, request_id: str | None = None, overrides: dict[str, object] | None = None):
        canonical = run_reader.get_run_binding(run_id)
        rid = request_id or f"cross-request-{uuid.uuid4().hex}"
        claims: dict[str, object] = {
            "iss": ISSUER,
            "aud": ASSERTION_AUDIENCE,
            "iat": now - 1,
            "exp": now + 45,
            "sub": f"run:{run_id}",
            "workload_issuer": "runtime-test",
            "tenant_id": canonical.tenant_id,
            "agent_id": canonical.agent_id,
            "release_id": canonical.release_id,
            "session_id": canonical.session_id,
            "run_id": canonical.run_id,
            "sandbox_id": canonical.sandbox_id,
            "sandbox_revision": canonical.sandbox_revision,
            "workspace_ref": canonical.workspace_ref,
            "binding_id": binding_id,
            "capability": "github",
            "audience": RESOURCE_AUDIENCE,
            "allowed_scopes": ["github:read"],
            "scopes": ["github:read"],
            "request_id": rid,
            "requested_ttl_seconds": 30,
            "approval_id": None,
        }
        if overrides:
            claims.update(overrides)
        assertion = _sign_assertion(private_key, claims)
        body = {
            "request_id": rid,
            "binding_id": binding_id,
            "audience": RESOURCE_AUDIENCE,
            "scopes": ["github:read"],
            "requested_ttl_seconds": 30,
            "approval_id": None,
            "workload_assertion": assertion,
        }
        return body

    context.update({
        "authority": authority,
        "request": request,
        "runtime_store": runtime_store,
        "run_reader": run_reader,
        "runtime_store_path": runtime_store_path,
        "reopen_runtime_authority": reopen_runtime_authority,
        "run_id": run_id,
        "session_id": session_id,
        "sandbox_id": sandbox_id,
        "workspace_ref": workspace_ref,
    })
    try:
        yield context
    finally:
        runtime_store.close()


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("tenant_id", "tenant-attacker"),
        ("agent_id", "agent-attacker"),
        ("release_id", "release-attacker"),
        ("session_id", "session-attacker"),
        ("sandbox_id", "sandbox-attacker"),
        ("sandbox_revision", 99),
        ("workspace_ref", "sandbox://kubernetes/sandbox-attacker"),
    ],
)
def test_signed_claim_cannot_override_runtime_store_authority(
    integration_context, field, forged_value
):
    request = integration_context["request"](overrides={field: forged_value})

    with pytest.raises(CredentialAuthorityDenied, match="canonical Run"):
        integration_context["authority"].issue(request)


def test_runtime_authority_can_issue_then_fence_credential_after_sandbox_rebind(
    integration_context,
):
    authority = integration_context["authority"]
    result = authority.issue(integration_context["request"]())
    credential = result["credential"]
    assert credential["run_id"] == integration_context["run_id"]
    assert credential["session_id"] == integration_context["session_id"]
    assert credential["sandbox_id"] == integration_context["sandbox_id"]
    assert credential["sandbox_revision"] == 7

    introspection_request = {
        "access_token": credential["access_token"],
        "audience": RESOURCE_AUDIENCE,
        "required_scopes": ["github:read"],
    }
    active = authority.introspect(introspection_request)["credential"]
    assert active["active"] is True

    store = integration_context["runtime_store"]
    run = store.get_run(integration_context["run_id"])
    old = run.sandbox_binding
    assert old is not None
    run.sandbox_binding = SandboxBinding(
        provider=old.provider,
        sandbox_id=old.sandbox_id,
        sandbox_ref=old.sandbox_ref,
        revision=old.revision + 1,
    )
    store.save_run(run)

    # The authoritative Run, Session and SandboxBinding must survive closing
    # the Runtime process/store and constructing a fresh canonical reader and
    # authority process. A same-process object graph is not enough evidence.
    integration_context["reopen_runtime_authority"]()
    authority = integration_context["authority"]
    assert integration_context["run_reader"].get_run_binding(
        integration_context["run_id"]
    ).sandbox_revision == 8

    inactive = authority.introspect(introspection_request)["credential"]
    assert inactive["active"] is False

    # The old, legitimately signed Sandbox revision cannot mint a new token
    # after RuntimeStore has advanced the canonical binding.
    stale_request = integration_context["request"](
        overrides={"sandbox_revision": 7}
    )
    with pytest.raises(CredentialAuthorityDenied, match="canonical Run"):
        authority.issue(stale_request)
