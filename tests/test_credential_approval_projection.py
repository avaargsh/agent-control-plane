from dataclasses import replace
from datetime import timedelta

import pytest

from agent_control_plane.policy_replay import (
    PolicyDecisionRecord,
    PolicyEffect,
    PolicyEvaluation,
    TransitionPolicyInput,
)
from agent_control_plane.state_transition_protocol import ActionIntent, Principal, ProtocolViolation
from agent_control_plane.transition_approval import (
    ApprovalDecision,
    ApprovalSigningKey,
    HMACApprovalVerifier,
    SignedTransitionApproval,
    TransitionApproval,
)
from agent_control_plane.workload_credentials import (
    CanonicalRunBinding,
    CredentialAuthorityDenied,
    CredentialBindingRecord,
    register_credential_approval_from_transition,
)
from test_kubernetes_deployment_transition import NOW, build_transition


APPROVER = Principal(type="human", subject="security-reviewer@example.test")
KEY = ApprovalSigningKey(
    key_id="credential-approval-test-v1",
    approver=APPROVER,
    secret=b"test-only-secret-for-credential-approval",
)
VERIFIER = HMACApprovalVerifier(keys={KEY.key_id: KEY})


class FakeRunReader:
    def __init__(self, run):
        self.run = run

    def get_run_binding(self, run_id):
        if run_id != self.run.run_id:
            raise LookupError("unknown Run")
        return self.run


class FakeApprovalStore:
    def __init__(self, binding):
        self.binding = binding
        self.grants = {}

    def get_binding(self, binding_id):
        return self.binding if binding_id == self.binding.binding_id else None

    def _insert_verified_approval(self, grant):
        if grant.approval_id in self.grants:
            raise ValueError("duplicate approval ID")
        self.grants[grant.approval_id] = grant
        return grant


def build_signed_credential_approval(*, canonical_run=None, params_override=None):
    fixture = build_transition()
    run = canonical_run or CanonicalRunBinding(
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
    binding = CredentialBindingRecord(
        binding_id="github-api",
        capability="github",
        audience="https://api.github.com",
        allowed_scopes=("github:read", "github:write"),
        max_ttl_seconds=90,
        active=True,
        version=4,
    )
    params = {
        "tenant_id": run.tenant_id,
        "agent_id": run.agent_id,
        "release_id": run.release_id,
        "session_id": run.session_id,
        "run_id": run.run_id,
        "sandbox_id": run.sandbox_id,
        "sandbox_revision": run.sandbox_revision,
        "workspace_ref": run.workspace_ref,
        "binding_id": binding.binding_id,
        "capability": binding.capability,
        "audience": binding.audience,
        "scopes": ["github:write"],
        "execution_principal_type": "agent",
        "execution_principal_subject": run.agent_id,
    }
    params.update(params_override or {})
    action = ActionIntent.seal(
        action_id="credential-issue-action-001",
        transition_hash=fixture["transition"].transition_hash,
        provider="agent-control-plane",
        operation="issue_workload_credential",
        parameters=params,
    )
    policy_input = TransitionPolicyInput.seal(
        policy_version="credential-policy/v1",
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=action,
        principal=Principal(type="agent", subject=run.agent_id),
        context={
            "operation": "issue_workload_credential",
            "run_id": run.run_id,
            "binding_id": binding.binding_id,
            "audience": binding.audience,
            "scopes": ["github:write"],
        },
    )
    policy_decision = PolicyDecisionRecord.seal(
        policy_input=policy_input,
        evaluation=PolicyEvaluation(effect=PolicyEffect.PERMIT, reasons=("exact credential action permitted",)),
    )
    approval = TransitionApproval.seal(
        approval_id="approval-credential-001",
        approver=APPROVER,
        decision=ApprovalDecision.APPROVE,
        transition=fixture["transition"],
        action=action,
        policy_input=policy_input,
        policy_decision=policy_decision,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        reason="Approve exact short-lived credential scope",
    )
    signed = SignedTransitionApproval.sign(approval, key=KEY)
    store = FakeApprovalStore(binding)
    reader = FakeRunReader(run)
    return store, reader, action, signed, run


def test_credential_approval_projection_requires_existing_signed_exact_transition():
    store, reader, action, signed, run = build_signed_credential_approval()
    grant = register_credential_approval_from_transition(
        store=store,
        run_authority=reader,
        signed_approval=signed,
        approval_verifier=VERIFIER,
        action=action,
        now=NOW + timedelta(seconds=1),
    )
    assert grant.approval_id == signed.approval.approval_id
    assert grant.tenant_id == run.tenant_id
    assert grant.agent_id == run.agent_id
    assert grant.session_id == run.session_id
    assert grant.run_id == run.run_id
    assert grant.scopes == ("github:write",)
    assert grant.approval_hash == signed.approval.approval_hash
    assert grant.action_hash == action.action_hash
    assert store.grants[grant.approval_id] is grant


@pytest.mark.parametrize(
    "override",
    [
        {"tenant_id": "tenant-attacker"},
        {"agent_id": "agent-attacker"},
        {"sandbox_id": "sandbox-attacker"},
        {"sandbox_revision": 2},
        {"workspace_ref": "sandbox://kubernetes/other-agent"},
        {"audience": "https://attacker.example"},
        {"scopes": ["github:admin"]},
    ],
)
def test_signed_approval_projection_denies_parameter_tampering(override):
    store, reader, action, signed, _ = build_signed_credential_approval(
        params_override=override,
    )
    with pytest.raises((CredentialAuthorityDenied, ProtocolViolation)):
        register_credential_approval_from_transition(
            store=store,
            run_authority=reader,
            signed_approval=signed,
            approval_verifier=VERIFIER,
            action=action,
            now=NOW + timedelta(seconds=1),
        )
    assert not store.grants


def test_signed_approval_projection_rejects_inactive_session_and_stale_sandbox_revision():
    paused_run = CanonicalRunBinding(
        tenant_id="tenant-a",
        agent_id="agent-a",
        release_id="release-a",
        session_id="session-a",
        run_id="run-a",
        sandbox_id="sandbox-a",
        sandbox_revision=3,
        workspace_ref="sandbox://kubernetes/sandbox-a",
        status="RUNNING",
        session_status="PAUSED",
    )
    store, reader, action, signed, _ = build_signed_credential_approval(canonical_run=paused_run)
    with pytest.raises(CredentialAuthorityDenied, match="active Session"):
        register_credential_approval_from_transition(
            store=store,
            run_authority=reader,
            signed_approval=signed,
            approval_verifier=VERIFIER,
            action=action,
            now=NOW + timedelta(seconds=1),
        )

    current_run = CanonicalRunBinding(
        tenant_id="tenant-a",
        agent_id="agent-a",
        release_id="release-a",
        session_id="session-a",
        run_id="run-a",
        sandbox_id="sandbox-replaced",
        sandbox_revision=4,
        workspace_ref="sandbox://kubernetes/sandbox-replaced",
        status="RUNNING",
        session_status="ACTIVE",
    )
    store, reader, action, signed, _ = build_signed_credential_approval(canonical_run=current_run)
    with pytest.raises(CredentialAuthorityDenied, match="canonical Run binding"):
        register_credential_approval_from_transition(
            store=store,
            run_authority=reader,
            signed_approval=signed,
            approval_verifier=VERIFIER,
            action=action,
            now=NOW + timedelta(seconds=1),
        )


def test_non_approve_signed_decision_cannot_be_projected():
    store, reader, action, signed, _ = build_signed_credential_approval()
    approval = replace(signed.approval, decision=ApprovalDecision.DENY, approval_hash="")
    # Re-seal the changed decision through the canonical constructor to ensure
    # the denial is semantic rather than merely a bad signature.
    fixture = build_transition()
    policy_input = TransitionPolicyInput.seal(
        policy_version=approval.policy_version,
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=action,
        principal=Principal(type="agent", subject="agent-a"),
        context={
            "operation": "issue_workload_credential",
            "run_id": "run-a",
            "binding_id": "github-api",
            "audience": "https://api.github.com",
            "scopes": ["github:write"],
        },
    )
    policy_decision = PolicyDecisionRecord.seal(
        policy_input=policy_input,
        evaluation=PolicyEvaluation(effect=PolicyEffect.PERMIT),
    )
    deny_approval = TransitionApproval.seal(
        approval_id="approval-credential-denied",
        approver=APPROVER,
        decision=ApprovalDecision.DENY,
        transition=fixture["transition"],
        action=action,
        policy_input=policy_input,
        policy_decision=policy_decision,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        reason="Denied",
    )
    deny_signed = SignedTransitionApproval.sign(deny_approval, key=KEY)
    with pytest.raises(CredentialAuthorityDenied, match="APPROVE"):
        register_credential_approval_from_transition(
            store=store,
            run_authority=reader,
            signed_approval=deny_signed,
            approval_verifier=VERIFIER,
            action=action,
            now=NOW + timedelta(seconds=1),
        )
    assert not store.grants
