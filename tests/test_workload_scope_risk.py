import pytest

from agent_control_plane.workload_credentials import (
    CanonicalRunBinding,
    CredentialAuthorityDenied,
    CredentialBindingRecord,
    WorkloadCredentialAuthority,
    _scope_needs_approval,
)


@pytest.mark.parametrize(
    "scope",
    [
        "github:repository:read",
        "github:pull_request:list",
        "kubernetes:deployment:get",
        "kubernetes:pod:describe",
        "metrics:inspect",
        "cloud:resource:READ",
    ],
)
def test_explicit_read_only_scope_verbs_do_not_require_approval(scope):
    assert _scope_needs_approval(scope) is False


@pytest.mark.parametrize(
    "scope",
    [
        "bank:transfer",
        "orders:create",
        "orders:update",
        "runtime:execute",
        "repository:publish",
        "workflow:approve",
        "kubernetes:deployment:write",
        "kubernetes:deployment:delete",
        "cloud:admin",
        "resource:mutate",
        "custom:unknown",
        "legacy-scope-with-no-action-verb",
    ],
)
def test_mutating_or_unknown_scope_verbs_require_approval(scope):
    assert _scope_needs_approval(scope) is True


def test_approval_exemption_depends_only_on_explicit_terminal_action_verb():
    # The scope name's final colon-delimited component is its action verb.
    # An unknown verb is never treated as read-only merely because it is not
    # present in a sensitive-verb denylist.
    assert _scope_needs_approval("payments:transfer") is True
    assert _scope_needs_approval("payments:read") is False



class _AssertionVerifier:
    def __init__(self, claims):
        self.claims = claims

    def verify(self, token, *, now):
        assert token == "signed-test-assertion"
        return self.claims


class _CredentialStore:
    def __init__(self, binding):
        self.binding = binding
        self.issue_calls = []
        self.approval_lookups = []

    def get_binding(self, binding_id):
        assert binding_id == self.binding.binding_id
        return self.binding

    def get_approval(self, approval_id):
        self.approval_lookups.append(approval_id)
        return None

    def issue_credential(self, **kwargs):
        self.issue_calls.append(kwargs)


class _RunAuthority:
    def __init__(self, binding):
        self.binding = binding

    def get_run_binding(self, run_id):
        assert run_id == self.binding.run_id
        return self.binding


def _build_issuer(scope):
    now = 1_800_000_000.0
    claims = {
        "sub": "workload-subject",
        "workload_issuer": "runtime-issuer",
        "tenant_id": "tenant-a",
        "agent_id": "agent-a",
        "release_id": "release-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "sandbox_id": "sandbox-a",
        "sandbox_revision": 3,
        "workspace_ref": "workspace-a",
        "binding_id": "binding-a",
        "capability": "payments-api",
        "audience": "payments-api",
        "request_id": "request-a",
        "requested_ttl_seconds": 60,
        "allowed_scopes": [scope],
        "scopes": [scope],
        "approval_id": None,
        "exp": now + 120,
    }
    binding = CredentialBindingRecord(
        binding_id="binding-a",
        capability="payments-api",
        audience="payments-api",
        allowed_scopes=(scope,),
        max_ttl_seconds=120,
        active=True,
        version=1,
    )
    canonical_run = CanonicalRunBinding(
        tenant_id="tenant-a",
        agent_id="agent-a",
        release_id="release-a",
        session_id="session-a",
        run_id="run-a",
        sandbox_id="sandbox-a",
        sandbox_revision=3,
        workspace_ref="workspace-a",
        status="RUNNING",
        session_status="ACTIVE",
    )
    store = _CredentialStore(binding)
    authority = WorkloadCredentialAuthority(
        store=store,
        assertion_verifier=_AssertionVerifier(claims),
        run_authority=_RunAuthority(canonical_run),
        clock=lambda: now,
        token_factory=lambda: "t" * 48,
    )
    body = {
        "request_id": "request-a",
        "binding_id": "binding-a",
        "audience": "payments-api",
        "scopes": [scope],
        "requested_ttl_seconds": 60,
        "workload_assertion": "signed-test-assertion",
    }
    return authority, store, body


def test_unknown_scope_is_denied_by_credential_issuance_boundary():
    authority, store, body = _build_issuer("payments:transfer")

    with pytest.raises(
        CredentialAuthorityDenied,
        match="sensitive credentials require an exact approval reference",
    ):
        authority.issue(body)

    assert store.issue_calls == []
    assert store.approval_lookups == []


def test_explicit_read_only_scope_can_be_issued_without_approval():
    authority, store, body = _build_issuer("payments:read")

    result = authority.issue(body)

    assert result["credential"]["scopes"] == ["payments:read"]
    assert len(store.issue_calls) == 1
    assert store.approval_lookups == []
