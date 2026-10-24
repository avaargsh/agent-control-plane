from __future__ import annotations

import base64
import io
import json
from dataclasses import replace

import pytest

from agent_control_plane.workload_credential_http import (
    ServiceRoleAuthenticator,
    WorkloadCredentialWSGI,
)
from agent_control_plane.workload_credentials import (
    ApprovalGrantRecord,
    ApprovalRevocationOutboxConsumer,
    CanonicalRunBinding,
    CredentialAuthorityConflict,
    CredentialAuthorityDenied,
    CredentialAuthorityNotFound,
    CredentialBindingRecord,
    CredentialRequestReplay,
    PinnedEd25519AssertionVerifier,
    WorkloadCredentialAuthority,
)


class FakeClock:
    def __init__(self, now=10_000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeAssertionVerifier:
    def __init__(self, claims):
        self.claims = claims

    def verify(self, token, *, now):
        if token != "signed-workload-assertion":
            raise CredentialAuthorityDenied("workload assertion is invalid")
        if self.claims["exp"] <= now:
            raise CredentialAuthorityDenied("workload assertion is expired")
        return dict(self.claims)


class FakeRunAuthority:
    def __init__(self, binding):
        self.binding = binding
        self.unavailable = False

    def get_run_binding(self, run_id):
        if self.unavailable:
            raise RuntimeError("simulated authority outage")
        if run_id != self.binding.run_id:
            raise CredentialAuthorityNotFound("unknown run")
        return self.binding


class FakeStore:
    """Transactional semantic double used for authority policy unit tests."""

    def __init__(self, binding):
        self.bindings = {binding.binding_id: binding}
        self.approvals = {}
        self.credentials = {}
        self.requests = {}
        self.revocation_events = {}
        self.outbox = {}

    def get_binding(self, binding_id):
        return self.bindings.get(binding_id)

    def get_approval(self, approval_id):
        return self.approvals.get(approval_id)

    def issue_credential(self, *, record, binding_version, approval_version, request_digest, now):
        key = (
            record["workload_issuer"],
            record["run_id"],
            record["binding_id"],
            record["request_id"],
        )
        if key in self.requests:
            raise CredentialRequestReplay("duplicate request")
        binding = self.bindings.get(record["binding_id"])
        if binding is None or not binding.active or binding.version != binding_version:
            raise CredentialAuthorityConflict("binding changed")
        if set(record["scopes"]) - set(binding.allowed_scopes):
            raise CredentialAuthorityConflict("scopes changed")
        approval_id = record.get("approval_id")
        if approval_id:
            approval = self.approvals.get(approval_id)
            if (
                approval is None
                or approval.state != "ACTIVE"
                or approval.version != approval_version
                or approval.expires_at <= now
            ):
                raise CredentialAuthorityDenied("approval changed")
        stored = dict(record)
        stored["revoked_at"] = None
        self.credentials[record["token_digest"]] = stored
        self.requests[key] = {
            "request_digest": request_digest,
            "credential_id": record["credential_id"],
        }

    def get_credential_by_digest(self, token_digest):
        record = self.credentials.get(token_digest)
        if record is None:
            return None
        result = dict(record)
        binding = self.bindings[record["binding_id"]]
        result["current_binding_active"] = binding.active
        result["current_binding_version"] = binding.version
        approval = self.approvals.get(record.get("approval_id"))
        result["current_approval_state"] = approval.state if approval else None
        result["current_approval_version"] = approval.version if approval else None
        result["current_approval_expires_at"] = approval.expires_at if approval else None
        return result

    def lookup_issue_request(self, *, issuer, run_id, binding_id, request_id, now):
        key = (issuer, run_id, binding_id, request_id)
        row = self.requests.get(key)
        if row is None:
            return None
        cred = next(
            value for value in self.credentials.values()
            if value["credential_id"] == row["credential_id"]
        )
        state = "REVOKED" if cred["revoked_at"] is not None else "ISSUED"
        if cred["expires_at"] <= now and state != "REVOKED":
            state = "EXPIRED"
        return {
            "issuer": issuer,
            "run_id": run_id,
            "binding_id": binding_id,
            "request_id": request_id,
            "credential_id": row["credential_id"],
            "state": state,
            "expires_at": cred["expires_at"],
        }

    def revoke_credential(self, credential_id, *, event_id, actor_role, now):
        prior = self.revocation_events.get(event_id)
        if prior:
            if prior["resource_type"] != "credential" or prior["resource_id"] != credential_id:
                raise CredentialAuthorityConflict("event ID rebound")
            return prior["count"] > 0
        changed = False
        for record in self.credentials.values():
            if record["credential_id"] == credential_id and record["revoked_at"] is None:
                record["revoked_at"] = now
                record["revoke_event_id"] = event_id
                record["revoked_by"] = actor_role
                changed = True
                break
        self.revocation_events[event_id] = {
            "resource_type": "credential",
            "resource_id": credential_id,
            "actor_role": actor_role,
            "count": int(changed),
        }
        return changed

    def revoke_run(self, run_id, *, sandbox_revision, event_id, actor_role, now):
        prior = self.revocation_events.get(event_id)
        if prior:
            if (
                prior["resource_type"] != "run"
                or prior["resource_id"] != run_id
                or prior["sandbox_revision"] != sandbox_revision
            ):
                raise CredentialAuthorityConflict("event ID rebound")
            return prior["count"]
        count = 0
        for record in self.credentials.values():
            if (
                record["run_id"] == run_id
                and record["sandbox_revision"] <= sandbox_revision
                and record["revoked_at"] is None
            ):
                record["revoked_at"] = now
                record["revoke_event_id"] = event_id
                record["revoked_by"] = actor_role
                count += 1
        self.revocation_events[event_id] = {
            "resource_type": "run",
            "resource_id": run_id,
            "sandbox_revision": sandbox_revision,
            "actor_role": actor_role,
            "count": count,
        }
        return count

    def revoke_approval(self, approval_id, *, event_id, actor_role, now):
        prior = self.revocation_events.get(event_id)
        if prior:
            if prior["resource_type"] != "approval" or prior["resource_id"] != approval_id:
                raise CredentialAuthorityConflict("event ID rebound")
            return prior["version"], prior["count"]
        grant = self.approvals.get(approval_id)
        if grant is None:
            raise CredentialAuthorityNotFound("unknown approval")
        version = grant.version if grant.state == "REVOKED" else grant.version + 1
        self.approvals[approval_id] = replace(grant, state="REVOKED", version=version)
        count = 0
        for record in self.credentials.values():
            if record.get("approval_id") == approval_id and record["revoked_at"] is None:
                record["revoked_at"] = now
                record["revoke_event_id"] = event_id
                record["revoked_by"] = actor_role
                count += 1
        self.revocation_events[event_id] = {
            "resource_type": "approval",
            "resource_id": approval_id,
            "actor_role": actor_role,
            "version": version,
            "count": count,
        }
        self.outbox[event_id] = {
            "event_id": event_id,
            "approval_id": approval_id,
            "approval_version": version,
            "payload": {
                "event_id": event_id,
                "approval_id": approval_id,
                "state": "REVOKED",
                "version": version,
            },
            "created_at": now,
            "attempts": 0,
            "available_at": now,
            "claim_id": None,
            "lease_until": None,
            "delivered_at": None,
            "last_error": None,
        }
        return version, count

    def claim_revocation_outbox(self, *, claim_id, now, lease_seconds, limit):
        rows = []
        for record in sorted(self.outbox.values(), key=lambda item: item["event_id"]):
            if len(rows) >= limit:
                break
            if record["delivered_at"] is not None or record["available_at"] > now:
                continue
            if record["lease_until"] is not None and record["lease_until"] > now:
                continue
            record["claim_id"] = claim_id
            record["lease_until"] = now + lease_seconds
            record["attempts"] += 1
            rows.append(dict(record))
        return rows

    def acknowledge_revocation_outbox(self, *, event_id, claim_id, delivered_at):
        record = self.outbox.get(event_id)
        if record is None or record["claim_id"] != claim_id or record["delivered_at"] is not None:
            return False
        record["delivered_at"] = delivered_at
        record["claim_id"] = None
        record["lease_until"] = None
        record["last_error"] = None
        return True

    def retry_revocation_outbox(self, *, event_id, claim_id, available_at, sanitized_error):
        record = self.outbox.get(event_id)
        if record is None or record["claim_id"] != claim_id or record["delivered_at"] is not None:
            return False
        record["available_at"] = available_at
        record["claim_id"] = None
        record["lease_until"] = None
        record["last_error"] = sanitized_error[:256]
        return True


def make_fixture(now=10_000.0):
    clock = FakeClock(now)
    binding = CredentialBindingRecord(
        binding_id="github-api",
        capability="github",
        audience="https://api.github.com",
        allowed_scopes=("github:read", "github:write"),
        max_ttl_seconds=90,
        active=True,
        version=1,
    )
    run = CanonicalRunBinding(
        tenant_id="tenant-a",
        agent_id="agent-a",
        release_id="release-a",
        session_id="session-a",
        run_id="run-a",
        sandbox_id="sandbox-a",
        sandbox_revision=3,
        workspace_ref="sandbox://kubernetes/sandbox-a",
        status="RUNNING",
        session_status="ACTIVE",
    )
    claims = {
        "iss": "https://runtime.example.test",
        "aud": "urn:acp:workload-credential-authority",
        "iat": now - 1,
        "exp": now + 100,
        "sub": "run:run-a",
        "workload_issuer": "runtime-prod",
        "tenant_id": "tenant-a",
        "agent_id": "agent-a",
        "release_id": "release-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "sandbox_id": "sandbox-a",
        "sandbox_revision": 3,
        "workspace_ref": "sandbox://kubernetes/sandbox-a",
        "binding_id": "github-api",
        "capability": "github",
        "audience": "https://api.github.com",
        "allowed_scopes": ["github:read"],
        "scopes": ["github:read"],
        "request_id": "request-1",
        "requested_ttl_seconds": 60,
        "approval_id": None,
    }
    store = FakeStore(binding)
    runs = FakeRunAuthority(run)
    authority = WorkloadCredentialAuthority(
        store=store,
        assertion_verifier=FakeAssertionVerifier(claims),
        run_authority=runs,
        clock=clock,
        token_factory=lambda: "opaque-token-that-is-long-enough-to-be-safe",
    )
    body = {
        "request_id": "request-1",
        "binding_id": "github-api",
        "audience": "https://api.github.com",
        "scopes": ["github:read"],
        "requested_ttl_seconds": 60,
        "approval_id": None,
        "workload_assertion": "signed-workload-assertion",
    }
    return clock, binding, run, claims, store, runs, authority, body


def make_approval(run, now, *, approval_id="approval-a", expires_at=None):
    return ApprovalGrantRecord(
        approval_id=approval_id,
        tenant_id=run.tenant_id,
        agent_id=run.agent_id,
        session_id=run.session_id,
        run_id=run.run_id,
        binding_id="github-api",
        audience="https://api.github.com",
        scopes=("github:write",),
        approved_by="human:security",
        issued_at=now - 1,
        expires_at=expires_at if expires_at is not None else now + 30,
        state="ACTIVE",
        version=1,
        approval_hash="sha256:" + "a" * 64,
        action_hash="sha256:" + "b" * 64,
        transition_hash="sha256:" + "c" * 64,
        policy_decision_hash="sha256:" + "d" * 64,
    )


def test_issue_and_introspection_bind_credential_to_canonical_run_and_revision():
    clock, _, run, claims, _, runs, authority, body = make_fixture()
    issued = authority.issue(body)["credential"]
    assert issued["run_id"] == run.run_id
    assert issued["sandbox_id"] == run.sandbox_id
    assert issued["sandbox_revision"] == run.sandbox_revision

    active = authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": ["github:read"],
    })["credential"]
    assert active["active"] is True
    assert active["workload_identity"]["session_id"] == run.session_id
    assert active["workload_identity"]["sandbox_revision"] == 3

    runs.binding = replace(run, sandbox_revision=4, sandbox_id="sandbox-b", workspace_ref="sandbox://kubernetes/sandbox-b")
    inactive = authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": [],
    })["credential"]
    assert inactive["active"] is False


def test_issue_denies_unsigned_claim_mismatch_and_cross_tenant_rebind():
    _, _, run, claims, _, runs, authority, body = make_fixture()
    claims["tenant_id"] = "tenant-b"
    with pytest.raises(CredentialAuthorityDenied, match="canonical Run"):
        authority.issue(body)

    _, _, run, claims, store, runs, authority, body = make_fixture()
    claims["sandbox_revision"] = 2
    with pytest.raises(CredentialAuthorityDenied, match="canonical Run"):
        authority.issue(body)


def test_signed_request_fields_cannot_change_audience_or_scope():
    _, _, _, claims, _, _, authority, body = make_fixture()
    claims["audience"] = "https://evil.example"
    with pytest.raises(CredentialAuthorityDenied, match="signed workload assertion"):
        authority.issue(body)

    _, _, _, claims, _, _, authority, body = make_fixture()
    body["scopes"] = ["github:write"]
    with pytest.raises(CredentialAuthorityDenied, match="signed workload assertion"):
        authority.issue(body)


def test_duplicate_request_id_is_durable_replay_rejection():
    _, _, _, _, _, _, authority, body = make_fixture()
    authority.issue(body)
    with pytest.raises(CredentialRequestReplay):
        authority.issue(body)
    # The reconciliation projection reports only request status and identity,
    # not the bearer token or token digest.
    result = authority.lookup_issue_request({
        "issuer": "runtime-prod",
        "run_id": "run-a",
        "binding_id": "github-api",
        "request_id": "request-1",
    })["request"]
    assert result["state"] == "ISSUED"
    assert "access_token" not in result
    assert "token_digest" not in result


def test_read_only_scopes_cannot_be_used_to_issue_write_credentials():
    _, _, _, claims, _, _, authority, body = make_fixture()
    body["scopes"] = ["github:write"]
    claims["scopes"] = ["github:write"]
    with pytest.raises(CredentialAuthorityDenied, match="binding audience or scopes"):
        authority.issue(body)


def test_sensitive_scope_requires_exact_active_approval_and_is_revoked_immediately():
    clock, _, run, claims, store, _, authority, body = make_fixture()
    claims["scopes"] = ["github:write"]
    claims["allowed_scopes"] = ["github:read", "github:write"]
    body["scopes"] = ["github:write"]
    body["approval_id"] = "approval-a"
    claims["approval_id"] = "approval-a"
    grant = make_approval(run, clock())
    store.approvals[grant.approval_id] = grant

    issued = authority.issue(body)["credential"]
    assert issued["expires_at"] <= grant.expires_at
    active = authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": ["github:write"],
    })["credential"]
    assert active["active"] is True

    version, revoked_count = store.revoke_approval(
        grant.approval_id,
        event_id="approval-revoked-1",
        actor_role="approval.revoker",
        now=clock(),
    )
    assert version == 2
    assert revoked_count == 1
    inactive = authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": ["github:write"],
    })["credential"]
    assert inactive["active"] is False


def test_approval_mismatch_expiry_and_run_status_change_fail_closed():
    clock, _, run, claims, store, runs, authority, body = make_fixture()
    claims["scopes"] = ["github:write"]
    claims["allowed_scopes"] = ["github:write"]
    claims["approval_id"] = "approval-a"
    body["scopes"] = ["github:write"]
    body["approval_id"] = "approval-a"
    wrong = replace(make_approval(run, clock()), agent_id="agent-b")
    store.approvals["approval-a"] = wrong
    with pytest.raises(CredentialAuthorityDenied, match="approval is missing"):
        authority.issue(body)

    expired = replace(make_approval(run, clock()), expires_at=clock() - 1)
    store.approvals["approval-a"] = expired
    with pytest.raises(CredentialAuthorityDenied, match="approval is missing"):
        authority.issue(body)

    _, _, run, claims, store, runs, authority, body = make_fixture()
    runs.binding = replace(run, session_status="PAUSED")
    with pytest.raises(CredentialAuthorityDenied, match="active Session"):
        authority.issue(body)


def test_binding_deactivation_or_policy_version_bump_invalidates_existing_tokens():
    _, _, _, _, store, _, authority, body = make_fixture()
    issued = authority.issue(body)["credential"]
    binding = store.bindings["github-api"]
    store.bindings["github-api"] = replace(binding, version=2)
    result = authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": [],
    })["credential"]
    assert result["active"] is False


def test_run_and_credential_revocation_are_immediate_and_idempotent():
    _, _, _, _, _, _, authority, body = make_fixture()
    issued = authority.issue(body)["credential"]
    revoke_body = {
        "credential_id": issued["credential_id"],
        "event_id": "revoke-credential-1",
        "_authenticated_service_role": "credential.revoker",
    }
    assert authority.revoke_credential(revoke_body)["revoked"] is True
    assert authority.revoke_credential(revoke_body)["revoked"] is True
    assert authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": [],
    })["credential"]["active"] is False

    _, _, _, _, store, _, authority, body = make_fixture()
    issued = authority.issue(body)["credential"]
    assert authority.revoke_run({
        "run_id": "run-a",
        "sandbox_revision": 3,
        "event_id": "revoke-run-1",
        "_authenticated_service_role": "credential.revoker",
    })["revoked_count"] == 1
    assert authority.introspect({
        "access_token": issued["access_token"],
        "audience": issued["audience"],
        "required_scopes": [],
    })["credential"]["active"] is False


def test_revocation_outbox_retries_and_acks_only_after_publish():
    clock, _, run, claims, store, _, authority, body = make_fixture()
    claims["scopes"] = ["github:write"]
    claims["allowed_scopes"] = ["github:write"]
    claims["approval_id"] = "approval-a"
    body["scopes"] = ["github:write"]
    body["approval_id"] = "approval-a"
    grant = make_approval(run, clock())
    store.approvals["approval-a"] = grant
    authority.issue(body)
    authority.revoke_approval({
        "approval_id": "approval-a",
        "event_id": "revocation-evt",
        "_authenticated_service_role": "credential.revoker",
    })
    assert store.outbox["revocation-evt"]["delivered_at"] is None

    class FlakyPublisher:
        def __init__(self):
            self.calls = 0

        def publish(self, payload):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("outage")
            assert payload["event_id"] == "revocation-evt"

    publisher = FlakyPublisher()
    consumer = ApprovalRevocationOutboxConsumer(
        store=store,
        publisher=publisher,
        clock=clock,
        lease_seconds=5,
        retry_seconds=2,
    )
    result = consumer.run_once()
    assert result == {"claimed": 1, "delivered": 0, "failed": 1}
    assert store.outbox["revocation-evt"]["delivered_at"] is None
    clock.now += 3
    result = consumer.run_once()
    assert result == {"claimed": 1, "delivered": 1, "failed": 0}
    assert store.outbox["revocation-evt"]["delivered_at"] is not None


def _jwt_part(data):
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")


def test_pinned_ed25519_verifier_rejects_tampered_and_untrusted_assertions():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    clock, _, _, claims, _, _, _, _ = make_fixture()
    private = Ed25519PrivateKey.generate()
    public_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    claims["iss"] = "https://runtime.example.test"
    claims["aud"] = "urn:acp:workload-credential-authority"
    claims["iat"] = clock() - 1
    claims["exp"] = clock() + 30
    header = {"alg": "EdDSA", "typ": "JWT", "kid": "operator-key-1"}
    first, second = _jwt_part(header), _jwt_part(claims)
    signing_input = f"{first}.{second}".encode("ascii")
    signature = base64.urlsafe_b64encode(private.sign(signing_input)).decode().rstrip("=")
    token = f"{first}.{second}.{signature}"

    verifier = PinnedEd25519AssertionVerifier(
        issuer="https://runtime.example.test",
        audience="urn:acp:workload-credential-authority",
        public_keys={"operator-key-1": public_bytes},
        max_lifetime_seconds=60,
    )
    assert verifier.verify(token, now=clock())["run_id"] == "run-a"
    tampered = f"{first}.{_jwt_part({**claims, 'tenant_id': 'tenant-b'})}.{signature}"
    with pytest.raises(CredentialAuthorityDenied):
        verifier.verify(tampered, now=clock())
    untrusted = PinnedEd25519AssertionVerifier(
        issuer="https://runtime.example.test",
        audience="urn:acp:workload-credential-authority",
        public_keys={"different-key": public_bytes},
        max_lifetime_seconds=60,
    )
    with pytest.raises(CredentialAuthorityDenied):
        untrusted.verify(token, now=clock())


def _invoke_wsgi(app, *, method, path, token, body=None, query="", scheme="https"):
    raw = b"" if body is None else json.dumps(body).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_TYPE": "application/json" if body is not None else "",
        "CONTENT_LENGTH": str(len(raw)) if body is not None else "",
        "wsgi.input": io.BytesIO(raw),
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "wsgi.url_scheme": scheme,
    }
    status = []
    headers = []

    def start_response(status_line, response_headers):
        status.append(status_line)
        headers.extend(response_headers)

    payload = b"".join(app(environ, start_response))
    return int(status[0].split()[0]), dict(headers), json.loads(payload)


def test_http_service_roles_are_separated_and_errors_are_sanitized():
    clock, _, _, _, _, _, authority, _ = make_fixture()
    tokens = {
        role: ServiceRoleAuthenticator.digest_token("long-test-token-" + role + "-0123456789")
        for role in (
            "credential.issuer",
            "credential.introspector",
            "credential.revoker",
            "approval.reader",
            "approval.revoker",
        )
    }
    authn = ServiceRoleAuthenticator(tokens)
    app = WorkloadCredentialWSGI(authority, authenticator=authn, clock=clock)

    status, headers, payload = _invoke_wsgi(
        app,
        method="POST",
        path="/v1/workload-credentials:issue",
        token="wrong-role-token-long-enough-0123456789",
        body={},
    )
    assert status == 403
    assert headers["Cache-Control"] == "no-store"

    status, _, payload = _invoke_wsgi(
        app,
        method="POST",
        path="/v1/workload-credentials:issue",
        token="long-test-token-credential.issuer-0123456789",
        body={},
    )
    assert status == 400
    assert "workload_assertion" not in json.dumps(payload)


def test_http_service_rejects_plain_http_even_with_valid_role_token():
    clock, _, _, _, _, _, authority, _ = make_fixture()
    token = "long-test-token-credential.issuer-0123456789"
    authn = ServiceRoleAuthenticator({
        "credential.issuer": ServiceRoleAuthenticator.digest_token(token),
    })
    app = WorkloadCredentialWSGI(authority, authenticator=authn, clock=clock)

    status, headers, payload = _invoke_wsgi(
        app,
        method="POST",
        path="/v1/workload-credentials:issue",
        token=token,
        body={},
        scheme="http",
    )

    assert status == 403
    assert payload["error"]["code"] == "secure_transport_required"
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_unrecognized_scope_verbs_require_approval_even_when_binding_allows_them():
    _, binding, _, claims, store, _, authority, body = make_fixture()
    store.bindings["github-api"] = replace(
        binding,
        allowed_scopes=("github:owner",),
    )
    claims["allowed_scopes"] = ["github:owner"]
    claims["scopes"] = ["github:owner"]
    body["scopes"] = ["github:owner"]
    # The signed caller cannot classify a new provider-specific verb as safe.
    with pytest.raises(CredentialAuthorityDenied, match="exact approval reference"):
        authority.issue(body)


def test_assertion_subject_must_be_the_canonical_run_subject():
    _, _, _, claims, _, _, authority, body = make_fixture()
    claims["sub"] = "run:another-agent-run"
    with pytest.raises(CredentialAuthorityDenied, match="subject"):
        authority.issue(body)



def test_credential_revoker_cannot_revoke_approval_authority():
    class StubAuthority:
        def revoke_approval(self, body):
            return {"revoked_count": 0}

    token_by_role = {
        role: "test-service-token-for-" + role + "-0123456789"
        for role in (
            "credential.issuer",
            "credential.introspector",
            "credential.revoker",
            "approval.reader",
            "approval.revoker",
        )
    }
    authenticator = ServiceRoleAuthenticator({
        role: ServiceRoleAuthenticator.digest_token(token)
        for role, token in token_by_role.items()
    })
    app = WorkloadCredentialWSGI(StubAuthority(), authenticator=authenticator, clock=lambda: 1.0)

    status, _, payload = _invoke_wsgi(
        app,
        method="POST",
        path="/v1/workload-credentials:revoke-approval",
        token=token_by_role["credential.revoker"],
        body={"approval_id": "approval-a", "event_id": "evt-a"},
    )
    assert status == 403
    assert payload["error"]["code"] == "service_role_denied"

    status, _, payload = _invoke_wsgi(
        app,
        method="POST",
        path="/v1/workload-credentials:revoke-approval",
        token=token_by_role["approval.revoker"],
        body={"approval_id": "approval-a", "event_id": "evt-a"},
    )
    assert status == 200
    assert payload == {"revoked_count": 0}


def test_service_role_authenticator_rejects_a_digest_shared_by_roles():
    shared_token = "shared-service-token-that-is-long-enough-0123456789"
    shared_digest = ServiceRoleAuthenticator.digest_token(shared_token)
    with pytest.raises(ValueError, match="distinct"):
        ServiceRoleAuthenticator({
            "credential.revoker": shared_digest,
            "approval.revoker": shared_digest,
        })
