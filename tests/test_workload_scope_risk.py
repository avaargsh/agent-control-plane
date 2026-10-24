import pytest

from agent_control_plane.workload_credentials import (
    ApprovalGrantRecord,
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
        "read",
        ":read",
        "payments:",
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
    def __init__(self, binding, approval=None):
        self.binding = binding
        self.approval = approval
        self.issue_calls = []
        self.approval_lookups = []

    def get_binding(self, binding_id):
        assert binding_id == self.binding.binding_id
        return self.binding

    def get_approval(self, approval_id):
        self.approval_lookups.append(approval_id)
        if self.approval is None or self.approval.approval_id != approval_id:
            return None
        return self.approval

    def issue_credential(self, **kwargs):
        self.issue_calls.append(kwargs)


class _RunAuthority:
    def __init__(self, binding):
        self.binding = binding

    def get_run_binding(self, run_id):
        assert run_id == self.binding.run_id
        return self.binding


def _build_issuer(scope, *, approval_id=None):
    scopes = [scope] if isinstance(scope, str) else list(scope)
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
        "allowed_scopes": scopes,
        "scopes": scopes,
        "approval_id": approval_id,
        "exp": now + 120,
    }
    binding = CredentialBindingRecord(
        binding_id="binding-a",
        capability="payments-api",
        audience="payments-api",
        allowed_scopes=tuple(scopes),
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
    approval = None
    if approval_id is not None:
        approval = ApprovalGrantRecord(
            approval_id=approval_id,
            tenant_id="tenant-a",
            agent_id="agent-a",
            session_id="session-a",
            run_id="run-a",
            binding_id="binding-a",
            audience="payments-api",
            scopes=tuple(scopes),
            approved_by="test-operator",
            issued_at=now - 1,
            expires_at=now + 30,
            state="ACTIVE",
            version=4,
        )
    store = _CredentialStore(binding, approval=approval)
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
        "scopes": scopes,
        "requested_ttl_seconds": 60,
        "workload_assertion": "signed-test-assertion",
        **({"approval_id": approval_id} if approval_id is not None else {}),
    }
    return authority, store, body


def test_unknown_scope_is_denied_by_credential_issuance_boundary():
    authority, store, body = _build_issuer("payments:transfer")

    with pytest.raises(
        CredentialAuthorityDenied,
        match="sensitive credential scopes require a matching scope-approval reference",
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



def test_unknown_scope_with_unresolvable_approval_is_not_issued():
    authority, store, body = _build_issuer(
        "payments:transfer", approval_id="missing-approval"
    )

    with pytest.raises(
        CredentialAuthorityDenied,
        match="approval is missing, expired, revoked or mismatched",
    ):
        authority.issue(body)

    assert store.approval_lookups == ["missing-approval"]
    assert store.issue_calls == []



def test_mixed_read_only_and_unknown_scopes_require_exact_approval():
    authority, store, body = _build_issuer(
        ["payments:read", "payments:transfer"]
    )

    with pytest.raises(
        CredentialAuthorityDenied,
        match="sensitive credential scopes require a matching scope-approval reference",
    ):
        authority.issue(body)

    assert store.issue_calls == []
    assert store.approval_lookups == []



def test_unknown_scope_with_exact_active_approval_can_be_issued():
    authority, store, body = _build_issuer(
        "payments:transfer", approval_id="approval-1"
    )

    result = authority.issue(body)

    assert result["credential"]["scopes"] == ["payments:transfer"]
    assert result["credential"]["expires_at"] == 1_800_000_030.0
    assert store.approval_lookups == ["approval-1"]
    assert len(store.issue_calls) == 1
    assert store.issue_calls[0]["approval_version"] == 4
