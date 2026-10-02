from dataclasses import replace
from datetime import timedelta

import pytest

from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentObserver,
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.kubernetes_transition_plan import (
    build_deployment_scale_plan,
    capture_deployment_scale_observation,
    validate_deployment_scale_plan,
)
from agent_control_plane.state_transition_protocol import (
    Principal,
    ProtocolViolation,
)
from kubernetes_testkit import NOW, build_transition


def _execute(fixture):
    provider = KubernetesDeploymentScaleProvider(fixture["api"])
    return provider.execute(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        caller=fixture["holder"],
        now=NOW + timedelta(seconds=1),
    )


def test_kubernetes_scale_projects_live_state_into_generic_plan():
    fixture = build_transition()
    live = fixture["api"].get_deployment(
        namespace="prod",
        name="payment-api",
    )
    observation = capture_deployment_scale_observation(
        subject=fixture["resource"],
        deployment=live,
        observer=fixture["holder"],
        observed_at=NOW + timedelta(seconds=1),
    )
    plan = build_deployment_scale_plan(
        transition=fixture["transition"],
        action=fixture["action"],
        observation=observation,
        created_at=NOW + timedelta(seconds=1),
    )

    validate_deployment_scale_plan(
        plan=plan,
        observation=observation,
        transition=fixture["transition"],
        action=fixture["action"],
    )

    assert observation.observed_version == "resourceVersion:100"
    assert observation.state["generation"] == 7
    assert observation.state["replicas"] == 20
    assert plan.before == {"replicas": 20}
    assert plan.desired == {"replicas": 30}
    assert plan.preconditions == {
        "observed_version": "resourceVersion:100",
        "generation": 7,
    }


def test_kubernetes_real_mutation_carries_exact_plan_hash():
    fixture = build_transition()

    receipt = _execute(fixture)

    annotations = fixture["api"].last_patch["metadata"]["annotations"]
    assert receipt.plan_hash is not None
    assert (
        annotations["agent-control-plane.openai.com/plan-hash"]
        == receipt.plan_hash
    )


def test_replay_preserves_original_plan_identity():
    fixture = build_transition()

    first = _execute(fixture)
    second = _execute(fixture)

    assert first.plan_hash is not None
    assert second.replayed is True
    assert second.plan_hash == first.plan_hash
    assert fixture["api"].patch_calls == 1


def test_provider_plan_rejects_generation_drift_before_mutation():
    fixture = build_transition()
    live = fixture["api"].get_deployment(
        namespace="prod",
        name="payment-api",
    )
    live["metadata"]["generation"] = 8
    observation = capture_deployment_scale_observation(
        subject=fixture["resource"],
        deployment=live,
        observer=fixture["holder"],
        observed_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(
        ProtocolViolation,
        match="resource generation changed before execution",
    ):
        build_deployment_scale_plan(
            transition=fixture["transition"],
            action=fixture["action"],
            observation=observation,
            created_at=NOW + timedelta(seconds=1),
        )


def test_plan_validation_rejects_operation_substitution():
    fixture = build_transition()
    live = fixture["api"].get_deployment(
        namespace="prod",
        name="payment-api",
    )
    observation = capture_deployment_scale_observation(
        subject=fixture["resource"],
        deployment=live,
        observer=fixture["holder"],
        observed_at=NOW + timedelta(seconds=1),
    )
    plan = build_deployment_scale_plan(
        transition=fixture["transition"],
        action=fixture["action"],
        observation=observation,
        created_at=NOW + timedelta(seconds=1),
    )
    changed_action = replace(
        fixture["action"],
        operation="delete_deployment",
    )

    with pytest.raises(
        ProtocolViolation,
        match="action intent digest mismatch",
    ):
        validate_deployment_scale_plan(
            plan=plan,
            observation=observation,
            transition=fixture["transition"],
            action=changed_action,
        )


def test_independent_kubernetes_observer_exposes_generic_snapshot():
    fixture = build_transition()
    observer_principal = Principal(
        type="controller",
        subject="independent-verifier",
    )
    observation = KubernetesDeploymentObserver(
        fixture["api"]
    ).observe(
        resource=fixture["resource"],
        principal=observer_principal,
        observed_at=NOW + timedelta(seconds=2),
    )

    observation.snapshot.verify()
    assert observation.snapshot.observer == observer_principal
    assert observation.snapshot.observed_version == "resourceVersion:100"
    assert observation.snapshot.state["replicas"] == 20
    assert observation.snapshot.state["readyReplicas"] == 20
