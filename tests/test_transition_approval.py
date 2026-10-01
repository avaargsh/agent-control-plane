from dataclasses import replace
from datetime import timedelta

import pytest

from agent_control_plane.policy_replay import (
    DeploymentScalePolicy,
    TransitionPolicyInput,
    evaluate_policy,
)
from agent_control_plane.state_transition_protocol import (
    ActionIntent,
    Principal,
    ProtocolViolation,
)
from agent_control_plane.transition_approval import (
    ApprovalDecision,
    ApprovalSigningKey,
    HMACApprovalVerifier,
    SignedTransitionApproval,
    TransitionApproval,
    authorize_transition_from_approval,
)
from test_kubernetes_deployment_transition import NOW, build_transition


APPROVER = Principal(
    type="human",
    subject="sre@example.test",
)
KEY = ApprovalSigningKey(
    key_id="sre-approval-key-v1",
    approver=APPROVER,
    secret=b"test-only-transition-approval-secret",
)


def build_policy_chain(*, max_replicas=30, policy_version="scale-policy/v1"):
    fixture = build_transition()
    policy_input = TransitionPolicyInput.seal(
        policy_version=policy_version,
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=fixture["action"],
        principal=Principal(
            type="agent",
            subject="autoscaler-agent",
        ),
        context={
            "operation": "scale_deployment",
            "namespace": "prod",
            "replicas": 30,
        },
    )
    decision = evaluate_policy(
        policy_input=policy_input,
        evaluator=DeploymentScalePolicy(
            policy_version=policy_version,
            max_replicas=max_replicas,
            allowed_namespaces=("prod",),
        ),
    )
    return fixture, policy_input, decision


def sign_approval(
    fixture,
    policy_input,
    policy_decision,
    *,
    decision=ApprovalDecision.APPROVE,
    issued_at=NOW,
    expires_at=None,
):
    approval = TransitionApproval.seal(
        approval_id="approval-payment-scale-001",
        approver=APPROVER,
        decision=decision,
        transition=fixture["transition"],
        action=fixture["action"],
        policy_input=policy_input,
        policy_decision=policy_decision,
        issued_at=issued_at,
        expires_at=expires_at or NOW + timedelta(minutes=10),
        reason="approved production scale change",
    )
    signed = SignedTransitionApproval.sign(
        approval,
        key=KEY,
    )
    verifier = HMACApprovalVerifier(
        keys={KEY.key_id: KEY},
    )
    return approval, signed, verifier


def test_signed_exact_approval_creates_authorization_binding():
    fixture, policy_input, policy_decision = build_policy_chain()
    approval, signed, verifier = sign_approval(
        fixture,
        policy_input,
        policy_decision,
    )

    authorization = authorize_transition_from_approval(
        transition=fixture["transition"],
        action=fixture["action"],
        policy_input=policy_input,
        policy_decision=policy_decision,
        signed_approval=signed,
        approval_verifier=verifier,
        authorization_expires_at=NOW + timedelta(minutes=5),
        now=NOW + timedelta(seconds=1),
    )

    assert authorization.approval_hash == approval.approval_hash
    assert authorization.action_hash == fixture["action"].action_hash
    assert (
        authorization.policy_decision_hash
        == policy_decision.decision_hash
    )
    assert authorization.principal == policy_input.principal


def test_approval_signature_binds_approver_identity():
    fixture, policy_input, policy_decision = build_policy_chain()
    approval = TransitionApproval.seal(
        approval_id="approval-payment-scale-001",
        approver=APPROVER,
        decision=ApprovalDecision.APPROVE,
        transition=fixture["transition"],
        action=fixture["action"],
        policy_input=policy_input,
        policy_decision=policy_decision,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    wrong_key = ApprovalSigningKey(
        key_id="wrong",
        approver=Principal(type="human", subject="other@example.test"),
        secret=b"wrong-secret",
    )

    with pytest.raises(
        ProtocolViolation,
        match="signer identity does not match approver",
    ):
        SignedTransitionApproval.sign(
            approval,
            key=wrong_key,
        )


def test_forged_signature_is_rejected():
    fixture, policy_input, policy_decision = build_policy_chain()
    _, signed, verifier = sign_approval(
        fixture,
        policy_input,
        policy_decision,
    )
    forged = replace(
        signed,
        signature="0" * 64,
    )

    with pytest.raises(
        ProtocolViolation,
        match="signature is invalid",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=policy_input,
            policy_decision=policy_decision,
            signed_approval=forged,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=1),
        )


def test_expired_approval_is_rejected():
    fixture, policy_input, policy_decision = build_policy_chain()
    _, signed, verifier = sign_approval(
        fixture,
        policy_input,
        policy_decision,
        issued_at=NOW - timedelta(minutes=10),
        expires_at=NOW - timedelta(seconds=1),
    )

    with pytest.raises(
        ProtocolViolation,
        match="approval expired",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=policy_input,
            policy_decision=policy_decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=1),
            now=NOW,
        )


def test_deny_approval_cannot_create_authorization():
    fixture, policy_input, policy_decision = build_policy_chain()
    _, signed, verifier = sign_approval(
        fixture,
        policy_input,
        policy_decision,
        decision=ApprovalDecision.DENY,
    )

    with pytest.raises(
        ProtocolViolation,
        match="decision is not APPROVE",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=policy_input,
            policy_decision=policy_decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=1),
        )


def test_human_approval_cannot_override_policy_deny():
    fixture, policy_input, policy_decision = build_policy_chain(
        max_replicas=20,
    )
    _, signed, verifier = sign_approval(
        fixture,
        policy_input,
        policy_decision,
        decision=ApprovalDecision.APPROVE,
    )

    with pytest.raises(
        ProtocolViolation,
        match="cannot override policy DENY",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=policy_input,
            policy_decision=policy_decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=1),
        )


def test_action_change_after_approval_is_rejected():
    fixture, policy_input, policy_decision = build_policy_chain()
    _, signed, verifier = sign_approval(
        fixture,
        policy_input,
        policy_decision,
    )
    changed_action = ActionIntent.seal(
        action_id=fixture["action"].action_id,
        transition_hash=fixture["transition"].transition_hash,
        provider="kubernetes",
        operation="scale_deployment",
        parameters={"replicas": 40},
    )

    with pytest.raises(
        ProtocolViolation,
        match="policy input action mismatch",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=changed_action,
            policy_input=policy_input,
            policy_decision=policy_decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=1),
        )


def test_policy_version_change_after_approval_is_rejected():
    fixture, policy_input, policy_decision = build_policy_chain()
    _, signed, verifier = sign_approval(
        fixture,
        policy_input,
        policy_decision,
    )
    changed_input = TransitionPolicyInput.seal(
        policy_version="scale-policy/v2",
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=fixture["action"],
        principal=policy_input.principal,
        context=policy_input.context,
    )
    changed_decision = evaluate_policy(
        policy_input=changed_input,
        evaluator=DeploymentScalePolicy(
            policy_version="scale-policy/v2",
            max_replicas=30,
            allowed_namespaces=("prod",),
        ),
    )

    with pytest.raises(
        ProtocolViolation,
        match="does not match authorization inputs",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=changed_input,
            policy_decision=changed_decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=1),
        )


def test_authorization_cannot_outlive_approval():
    fixture, policy_input, policy_decision = build_policy_chain()
    _, signed, verifier = sign_approval(
        fixture,
        policy_input,
        policy_decision,
        expires_at=NOW + timedelta(minutes=2),
    )

    with pytest.raises(
        ProtocolViolation,
        match="cannot outlive transition approval",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=policy_input,
            policy_decision=policy_decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=3),
            now=NOW + timedelta(seconds=1),
        )


def test_tampered_approval_content_is_rejected_before_signature_check():
    fixture, policy_input, policy_decision = build_policy_chain()
    approval, signed, verifier = sign_approval(
        fixture,
        policy_input,
        policy_decision,
    )
    object.__setattr__(
        approval,
        "generation",
        approval.generation + 1,
    )

    with pytest.raises(
        ProtocolViolation,
        match="approval digest mismatch",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=policy_input,
            policy_decision=policy_decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=1),
        )


def test_unknown_approval_signing_key_is_rejected():
    fixture, policy_input, policy_decision = build_policy_chain()
    _, signed, _ = sign_approval(
        fixture,
        policy_input,
        policy_decision,
    )
    verifier = HMACApprovalVerifier(keys={})

    with pytest.raises(
        ProtocolViolation,
        match="signing key is not trusted",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=policy_input,
            policy_decision=policy_decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=1),
        )


def test_execution_principal_change_after_approval_is_rejected():
    fixture, policy_input, policy_decision = build_policy_chain()
    _, signed, verifier = sign_approval(
        fixture,
        policy_input,
        policy_decision,
    )
    changed_input = TransitionPolicyInput.seal(
        policy_version=policy_input.policy_version,
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=fixture["action"],
        principal=Principal(
            type="agent",
            subject="different-agent",
        ),
        context=policy_input.context,
    )
    changed_decision = evaluate_policy(
        policy_input=changed_input,
        evaluator=DeploymentScalePolicy(
            policy_version=policy_input.policy_version,
            max_replicas=30,
            allowed_namespaces=("prod",),
        ),
    )

    with pytest.raises(
        ProtocolViolation,
        match="does not match authorization inputs",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=changed_input,
            policy_decision=changed_decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=1),
        )


def test_trusted_registry_key_id_mismatch_is_rejected():
    fixture, policy_input, policy_decision = build_policy_chain()
    _, signed, _ = sign_approval(
        fixture,
        policy_input,
        policy_decision,
    )
    mismatched = ApprovalSigningKey(
        key_id="different-internal-key-id",
        approver=APPROVER,
        secret=KEY.secret,
    )
    verifier = HMACApprovalVerifier(
        keys={signed.key_id: mismatched},
    )

    with pytest.raises(
        ProtocolViolation,
        match="trusted key id mismatch",
    ):
        authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=policy_input,
            policy_decision=policy_decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=1),
        )
