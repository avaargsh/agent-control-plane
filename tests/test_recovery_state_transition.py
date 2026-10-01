from datetime import timedelta

import pytest

from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentObserver,
    KubernetesDeploymentScaleProvider,
    VerificationStatus,
    verify_outcome,
)
from agent_control_plane.recovery import (
    RecoveryDisposition,
    RecoveryPolicy,
    plan_recovery_transition,
)
from agent_control_plane.state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    ExecutionFence,
    ExecutionLease,
    OutcomeCondition,
    OutcomeContract,
    Principal,
    ProtocolViolation,
)
from test_kubernetes_deployment_transition import (
    NOW,
    build_transition,
    execute,
    observe,
)


def _failed_verification(fixture):
    execute(fixture)
    failure_observation = observe(fixture)
    result = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=failure_observation,
        safety_metrics={
            "error_rate": 0.002,
            "p95_ms": 210,
        },
        checked_at=NOW + timedelta(seconds=3),
    )
    assert result.status is VerificationStatus.DEGRADED
    return failure_observation, result


def _recovery_contract():
    return OutcomeContract.seal(
        desired_conditions=(
            OutcomeCondition(
                source="kubernetes",
                expression="deployment.status.readyReplicas",
                comparator="eq",
                expected=20,
            ),
        ),
        safety_conditions=(
            OutcomeCondition(
                source="prometheus",
                expression="error_rate",
                comparator="lt",
                expected=0.01,
            ),
        ),
        stabilization_seconds=0,
        deadline_seconds=300,
    )


def test_failed_scale_plans_restore_as_new_state_transition():
    fixture = build_transition()
    failure_observation, result = _failed_verification(fixture)
    policy = RecoveryPolicy(
        name="deployment-scale-recovery",
        version="v1",
    )

    plan = plan_recovery_transition(
        source_transition=fixture["transition"],
        trigger_status=result.status.value,
        failure_evidence=failure_observation.evidence_bundle,
        outcome_contract=_recovery_contract(),
        policy=policy,
        created_at=NOW + timedelta(seconds=4),
        transition_id="recover-payment-api-30-20",
    )

    assert plan.disposition is RecoveryDisposition.RECOVER
    assert plan.policy_ref == "deployment-scale-recovery@v1"
    recovery = plan.recovery_transition
    assert recovery is not None
    assert recovery.expected_generation == 8
    assert recovery.before == {"replicas": 30}
    assert recovery.desired == {"replicas": 20}
    assert (
        recovery.evidence_hash
        == failure_observation.evidence_bundle.manifest_hash
    )
    assert recovery.transition_hash != fixture["transition"].transition_hash


def test_unknown_verification_freezes_instead_of_auto_recovery():
    fixture = build_transition()
    execute(fixture)
    observation = observe(fixture)
    result = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=observation,
        safety_metrics={"error_rate": 0.002},
        checked_at=NOW + timedelta(seconds=3),
    )
    assert result.status is VerificationStatus.UNKNOWN

    plan = plan_recovery_transition(
        source_transition=fixture["transition"],
        trigger_status=result.status.value,
        failure_evidence=observation.evidence_bundle,
        outcome_contract=_recovery_contract(),
        policy=RecoveryPolicy(
            name="deployment-scale-recovery",
            version="v1",
        ),
        created_at=NOW + timedelta(seconds=4),
        transition_id="must-not-exist",
    )

    assert plan.disposition is RecoveryDisposition.FREEZE
    assert plan.recovery_transition is None


def test_recovery_requires_post_transition_evidence():
    fixture = build_transition()

    with pytest.raises(
        ProtocolViolation,
        match="must observe a generation after the source transition",
    ):
        plan_recovery_transition(
            source_transition=fixture["transition"],
            trigger_status="DEGRADED",
            failure_evidence=fixture["evidence"],
            outcome_contract=_recovery_contract(),
            policy=RecoveryPolicy(
                name="deployment-scale-recovery",
                version="v1",
            ),
            created_at=NOW + timedelta(seconds=4),
            transition_id="invalid-recovery",
        )


def test_original_authorization_cannot_authorize_recovery_transition():
    fixture = build_transition()
    failure_observation, result = _failed_verification(fixture)
    contract = _recovery_contract()
    plan = plan_recovery_transition(
        source_transition=fixture["transition"],
        trigger_status=result.status.value,
        failure_evidence=failure_observation.evidence_bundle,
        outcome_contract=contract,
        policy=RecoveryPolicy(
            name="deployment-scale-recovery",
            version="v1",
        ),
        created_at=NOW + timedelta(seconds=4),
        transition_id="recover-payment-api-30-20",
    )
    recovery = plan.recovery_transition
    assert recovery is not None
    action = ActionIntent.seal(
        action_id="action-recover-payment-api-30-20",
        transition_hash=recovery.transition_hash,
        provider="kubernetes",
        operation="scale_deployment",
        parameters={"replicas": 20},
    )

    with pytest.raises(
        ProtocolViolation,
        match="action is not bound to transition",
    ):
        AuthorizationBinding.seal(
            transition=fixture["transition"],
            action=action,
            policy_version="opa:recovery@sha256:new",
            policy_decision_hash="sha256:" + "c" * 64,
            approval_hash="sha256:" + "d" * 64,
            principal=Principal(
                type="controller",
                subject="recovery-controller",
            ),
            expires_at=NOW + timedelta(minutes=5),
        )


def test_fail_recover_verify_uses_fresh_authorization_and_new_lease_epoch():
    fixture = build_transition()
    failure_observation, result = _failed_verification(fixture)
    contract = _recovery_contract()
    policy = RecoveryPolicy(
        name="deployment-scale-recovery",
        version="v1",
    )
    plan = plan_recovery_transition(
        source_transition=fixture["transition"],
        trigger_status=result.status.value,
        failure_evidence=failure_observation.evidence_bundle,
        outcome_contract=contract,
        policy=policy,
        created_at=NOW + timedelta(seconds=4),
        transition_id="recover-payment-api-30-20",
    )
    recovery = plan.recovery_transition
    assert recovery is not None

    action = ActionIntent.seal(
        action_id="action-recover-payment-api-30-20",
        transition_hash=recovery.transition_hash,
        provider="kubernetes",
        operation="scale_deployment",
        parameters={"replicas": 20},
    )
    authorization = AuthorizationBinding.seal(
        transition=recovery,
        action=action,
        policy_version="opa:recovery-policy@sha256:new",
        policy_decision_hash="sha256:" + "c" * 64,
        approval_hash="sha256:" + "d" * 64,
        principal=Principal(
            type="controller",
            subject="recovery-controller",
        ),
        expires_at=NOW + timedelta(minutes=5),
    )
    recovery_holder = Principal(
        type="controller",
        subject="agent-control-plane/recovery-controller",
    )
    lease = ExecutionLease(
        lease_id="lease-payment-api-recovery",
        resource_uid=fixture["resource"].resource_uid,
        holder=recovery_holder,
        epoch=fixture["lease"].epoch + 1,
        acquired_at=NOW + timedelta(seconds=4),
        expires_at=NOW + timedelta(minutes=10),
    )
    fence = ExecutionFence.bind(
        transition=recovery,
        action=action,
        authorization=authorization,
        lease=lease,
    )

    receipt = KubernetesDeploymentScaleProvider(fixture["api"]).execute(
        transition=recovery,
        evidence=failure_observation.evidence_bundle,
        outcome_contract=contract,
        action=action,
        authorization=authorization,
        fence=fence,
        active_lease=lease,
        caller=recovery_holder,
        now=NOW + timedelta(seconds=5),
    )

    assert receipt.changed is True
    assert receipt.desired_replicas == 20
    assert receipt.before_generation == 8
    assert receipt.after_generation == 9
    assert fixture["api"].deployment["spec"]["replicas"] == 20
    assert fixture["api"].patch_calls == 2

    recovery_observation = KubernetesDeploymentObserver(
        fixture["api"]
    ).observe(
        resource=fixture["resource"],
        principal=fixture["collector"],
        observed_at=NOW + timedelta(seconds=6),
    )
    recovered = verify_outcome(
        transition=recovery,
        outcome_contract=contract,
        observation=recovery_observation,
        safety_metrics={"error_rate": 0.002},
        checked_at=NOW + timedelta(seconds=7),
    )

    assert recovered.status is VerificationStatus.SUCCEEDED
