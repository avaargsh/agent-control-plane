from datetime import timedelta

import pytest

from agent_control_plane.policy_replay import (
    DeploymentScalePolicy,
    PolicyEffect,
    PolicyReplayMismatch,
    TransitionPolicyInput,
    evaluate_policy,
    replay_policy_decision,
)
from agent_control_plane.state_transition_protocol import (
    AuthorizationBinding,
    Principal,
    ProtocolViolation,
)
from test_kubernetes_deployment_transition import NOW, build_transition


def build_input(*, replicas=30, context=None, policy_version="scale-policy/v1"):
    fixture = build_transition()
    if replicas != 30:
        action = fixture["action"].seal(
            action_id=f"action-scale-{replicas}",
            transition_hash=fixture["transition"].transition_hash,
            provider="kubernetes",
            operation="scale_deployment",
            parameters={"replicas": replicas},
        )
    else:
        action = fixture["action"]

    policy_context = context or {
        "operation": "scale_deployment",
        "namespace": "prod",
        "replicas": replicas,
    }
    policy_input = TransitionPolicyInput.seal(
        policy_version=policy_version,
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=action,
        principal=Principal(
            type="agent",
            subject="autoscaler-agent",
        ),
        context=policy_context,
    )
    return fixture, action, policy_input


def test_identical_frozen_policy_input_replays_to_identical_decision_hash():
    fixture, action, policy_input = build_input()
    policy = DeploymentScalePolicy(
        policy_version="scale-policy/v1",
        max_replicas=30,
        allowed_namespaces=("prod",),
    )

    original = evaluate_policy(
        policy_input=policy_input,
        evaluator=policy,
    )
    replayed = replay_policy_decision(
        original=original,
        policy_input=policy_input,
        evaluator=policy,
    )

    assert original.effect is PolicyEffect.PERMIT
    assert replayed == original
    assert replayed.decision_hash == original.decision_hash

    authorization = AuthorizationBinding.seal(
        transition=fixture["transition"],
        action=action,
        policy_version=policy_input.policy_version,
        policy_decision_hash=original.decision_hash,
        approval_hash="sha256:" + "b" * 64,
        principal=Principal(
            type="agent",
            subject="autoscaler-agent",
        ),
        expires_at=NOW + timedelta(minutes=5),
    )
    assert authorization.policy_decision_hash == original.decision_hash


def test_policy_input_hash_is_independent_of_context_key_order():
    fixture = build_transition()
    principal = Principal(type="agent", subject="autoscaler-agent")

    left = TransitionPolicyInput.seal(
        policy_version="scale-policy/v1",
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=fixture["action"],
        principal=principal,
        context={
            "operation": "scale_deployment",
            "namespace": "prod",
            "replicas": 30,
        },
    )
    right = TransitionPolicyInput.seal(
        policy_version="scale-policy/v1",
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=fixture["action"],
        principal=principal,
        context={
            "replicas": 30,
            "namespace": "prod",
            "operation": "scale_deployment",
        },
    )

    assert left.context_json == right.context_json
    assert left.input_hash == right.input_hash


def test_policy_replay_rejects_changed_frozen_input():
    _, _, original_input = build_input()
    policy = DeploymentScalePolicy(
        policy_version="scale-policy/v1",
        max_replicas=30,
        allowed_namespaces=("prod",),
    )
    original = evaluate_policy(
        policy_input=original_input,
        evaluator=policy,
    )
    _, _, changed_input = build_input(
        context={
            "operation": "scale_deployment",
            "namespace": "prod",
            "replicas": 29,
        }
    )

    with pytest.raises(
        PolicyReplayMismatch,
        match="policy input changed during replay",
    ):
        replay_policy_decision(
            original=original,
            policy_input=changed_input,
            evaluator=policy,
        )


def test_policy_replay_rejects_changed_policy_version():
    _, _, policy_input = build_input()
    original = evaluate_policy(
        policy_input=policy_input,
        evaluator=DeploymentScalePolicy(
            policy_version="scale-policy/v1",
            max_replicas=30,
            allowed_namespaces=("prod",),
        ),
    )
    _, _, changed_input = build_input(
        policy_version="scale-policy/v2",
    )

    with pytest.raises(
        PolicyReplayMismatch,
        match="policy version changed during replay",
    ):
        replay_policy_decision(
            original=original,
            policy_input=changed_input,
            evaluator=DeploymentScalePolicy(
                policy_version="scale-policy/v2",
                max_replicas=30,
                allowed_namespaces=("prod",),
            ),
        )


def test_same_policy_version_with_changed_semantics_is_detected():
    _, _, policy_input = build_input()
    original = evaluate_policy(
        policy_input=policy_input,
        evaluator=DeploymentScalePolicy(
            policy_version="scale-policy/v1",
            max_replicas=30,
            allowed_namespaces=("prod",),
        ),
    )

    with pytest.raises(
        PolicyReplayMismatch,
        match="decision diverged",
    ):
        replay_policy_decision(
            original=original,
            policy_input=policy_input,
            evaluator=DeploymentScalePolicy(
                policy_version="scale-policy/v1",
                max_replicas=20,
                allowed_namespaces=("prod",),
            ),
        )


def test_scale_policy_denial_is_deterministic():
    _, _, policy_input = build_input(
        context={
            "operation": "scale_deployment",
            "namespace": "prod",
            "replicas": 40,
        }
    )
    policy = DeploymentScalePolicy(
        policy_version="scale-policy/v1",
        max_replicas=30,
        allowed_namespaces=("prod",),
    )

    first = evaluate_policy(
        policy_input=policy_input,
        evaluator=policy,
    )
    second = replay_policy_decision(
        original=first,
        policy_input=policy_input,
        evaluator=policy,
    )

    assert first.effect is PolicyEffect.DENY
    assert first.reasons == ("REPLICA_LIMIT_EXCEEDED",)
    assert second.decision_hash == first.decision_hash


def test_evaluator_version_must_match_frozen_policy_version():
    _, _, policy_input = build_input()

    with pytest.raises(
        ProtocolViolation,
        match="evaluator version does not match",
    ):
        evaluate_policy(
            policy_input=policy_input,
            evaluator=DeploymentScalePolicy(
                policy_version="scale-policy/v2",
                max_replicas=30,
                allowed_namespaces=("prod",),
            ),
        )


def test_policy_input_cannot_bind_action_from_another_transition():
    fixture = build_transition()
    other = fixture["action"].seal(
        action_id="unbound-action",
        transition_hash="sha256:" + "f" * 64,
        provider="kubernetes",
        operation="scale_deployment",
        parameters={"replicas": 30},
    )

    with pytest.raises(
        ProtocolViolation,
        match="action is not bound to transition",
    ):
        TransitionPolicyInput.seal(
            policy_version="scale-policy/v1",
            evidence=fixture["evidence"],
            transition=fixture["transition"],
            action=other,
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
