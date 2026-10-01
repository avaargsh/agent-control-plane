from datetime import datetime, timedelta, timezone

import pytest

from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentObserver,
    KubernetesDeploymentScaleProvider,
    VerificationStatus,
    verify_outcome,
)
from agent_control_plane.runtime_clients import (
    RuntimeMutationOwnershipUncertain,
    RuntimeMutationUncertain,
)
from agent_control_plane.state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    EvidenceBundle,
    EvidenceItem,
    ExecutionFence,
    ExecutionLease,
    OutcomeCondition,
    OutcomeContract,
    Principal,
    ProtocolViolation,
    ResourceIdentity,
    StateTransition,
)


NOW = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)


class FakeDeploymentApi:
    def __init__(self):
        self.patch_calls = 0
        self.pod_calls = 0
        self.event_calls = 0
        self.last_patch = None
        self.deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "payment-api",
                "namespace": "prod",
                "uid": "uid-payment-api",
                "generation": 7,
                "resourceVersion": "100",
                "annotations": {},
            },
            "spec": {
                "replicas": 20,
                "selector": {
                    "matchLabels": {
                        "app": "payment-api",
                    },
                },
            },
            "status": {
                "replicas": 20,
                "readyReplicas": 20,
                "availableReplicas": 20,
            },
        }
        self.pods = [
            {
                "metadata": {
                    "name": "payment-api-a",
                    "uid": "pod-a",
                },
                "status": {"phase": "Running"},
            },
        ]
        self.events = [
            {
                "metadata": {"name": "scale-event"},
                "reason": "ScalingReplicaSet",
            },
        ]

    def get_deployment(self, *, namespace, name):
        if (
            namespace != self.deployment["metadata"]["namespace"]
            or name != self.deployment["metadata"]["name"]
        ):
            return None
        return self._copy(self.deployment)

    def patch_deployment(self, *, namespace, name, patch):
        self.patch_calls += 1
        self.last_patch = self._copy(patch)
        if namespace != "prod" or name != "payment-api":
            raise AssertionError("unexpected deployment identity")
        expected_rv = patch["metadata"]["resourceVersion"]
        if expected_rv != self.deployment["metadata"]["resourceVersion"]:
            raise RuntimeError("resourceVersion conflict")

        self.deployment["metadata"]["annotations"].update(
            patch["metadata"].get("annotations", {})
        )
        new_replicas = patch["spec"]["replicas"]
        if new_replicas != self.deployment["spec"]["replicas"]:
            self.deployment["spec"]["replicas"] = new_replicas
            self.deployment["metadata"]["generation"] += 1
        self.deployment["metadata"]["resourceVersion"] = str(
            int(self.deployment["metadata"]["resourceVersion"]) + 1
        )
        return self._copy(self.deployment)

    def list_pods(self, *, namespace, selector):
        self.pod_calls += 1
        assert namespace == "prod"
        assert selector == "app=payment-api"
        return [self._copy(item) for item in self.pods]

    def list_events(self, *, namespace, involved_object_uid):
        self.event_calls += 1
        assert namespace == "prod"
        assert involved_object_uid == "uid-payment-api"
        return [self._copy(item) for item in self.events]

    @staticmethod
    def _copy(value):
        import json
        return json.loads(json.dumps(value))


class LostAckDeploymentApi(FakeDeploymentApi):
    def patch_deployment(self, *, namespace, name, patch):
        super().patch_deployment(
            namespace=namespace,
            name=name,
            patch=patch,
        )
        raise RuntimeMutationUncertain("connection reset after commit")


class LostAckOwnedByOtherApi(FakeDeploymentApi):
    def patch_deployment(self, *, namespace, name, patch):
        other = self._copy(patch)
        other["metadata"]["annotations"][
            "agent-control-plane.openai.com/operation-id"
        ] = "other-controller"
        super().patch_deployment(
            namespace=namespace,
            name=name,
            patch=other,
        )
        raise RuntimeMutationUncertain("connection reset after ambiguous commit")


def build_transition(api=None):
    api = api or FakeDeploymentApi()
    resource = ResourceIdentity(
        provider="kubernetes",
        resource_uid="uid-payment-api",
        namespace="prod",
        kind="Deployment",
        name="payment-api",
    )
    collector = Principal(
        type="service_account",
        subject="system:serviceaccount:agentplane:evidence-collector",
    )
    initial = api.get_deployment(
        namespace="prod",
        name="payment-api",
    )
    evidence_item = EvidenceItem.capture(
        evidence_id="deployment-before",
        evidence_type="kubernetes_deployment",
        source="kubernetes://prod/deployment/payment-api",
        collected_at=NOW,
        collector_name="kubernetes-deployment-observer",
        collector_version="v1",
        principal=collector,
        query="get deployment/payment-api",
        payload=initial,
    )
    evidence = EvidenceBundle.seal(
        resource=resource,
        generation=7,
        created_at=NOW,
        items=(evidence_item,),
    )
    outcome = OutcomeContract.seal(
        desired_conditions=(
            OutcomeCondition(
                source="kubernetes",
                expression="deployment.status.readyReplicas",
                comparator="eq",
                expected=30,
            ),
        ),
        safety_conditions=(
            OutcomeCondition(
                source="prometheus",
                expression="error_rate",
                comparator="lt",
                expected=0.01,
            ),
            OutcomeCondition(
                source="prometheus",
                expression="p95_ms",
                comparator="lt",
                expected=500,
            ),
        ),
        stabilization_seconds=0,
        deadline_seconds=300,
    )
    transition = StateTransition.seal(
        transition_id="tr-payment-scale-20-30",
        subject=resource,
        expected_generation=7,
        before={"replicas": 20},
        desired={"replicas": 30},
        evidence_hash=evidence.manifest_hash,
        outcome_contract_hash=outcome.contract_hash,
        created_at=NOW,
    )
    action = ActionIntent.seal(
        action_id="action-payment-scale-20-30",
        transition_hash=transition.transition_hash,
        provider="kubernetes",
        operation="scale_deployment",
        parameters={"replicas": 30},
    )
    authorization = AuthorizationBinding.seal(
        transition=transition,
        action=action,
        policy_version="opa:scale-policy@sha256:abc",
        policy_decision_hash="sha256:" + "a" * 64,
        approval_hash="sha256:" + "b" * 64,
        principal=Principal(
            type="agent",
            subject="autoscaler-agent",
        ),
        expires_at=NOW + timedelta(minutes=5),
    )
    holder = Principal(
        type="controller",
        subject="agent-control-plane/controller-a",
    )
    lease = ExecutionLease(
        lease_id="lease-payment-api",
        resource_uid=resource.resource_uid,
        holder=holder,
        epoch=9,
        acquired_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    fence = ExecutionFence.bind(
        transition=transition,
        action=action,
        authorization=authorization,
        lease=lease,
    )
    return {
        "api": api,
        "resource": resource,
        "collector": collector,
        "evidence": evidence,
        "outcome": outcome,
        "transition": transition,
        "action": action,
        "authorization": authorization,
        "holder": holder,
        "lease": lease,
        "fence": fence,
    }


def execute(fixture):
    return KubernetesDeploymentScaleProvider(fixture["api"]).execute(
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


def observe(fixture, *, at=None):
    return KubernetesDeploymentObserver(fixture["api"]).observe(
        resource=fixture["resource"],
        principal=fixture["collector"],
        observed_at=at or NOW + timedelta(seconds=2),
    )


def test_scale_transition_executes_resource_version_bound_patch():
    fixture = build_transition()

    receipt = execute(fixture)

    assert receipt.changed is True
    assert receipt.replayed is False
    assert receipt.desired_replicas == 30
    assert receipt.before_resource_version == "100"
    assert receipt.after_resource_version == "101"
    assert receipt.before_generation == 7
    assert receipt.after_generation == 8
    assert fixture["api"].last_patch["metadata"]["resourceVersion"] == "100"
    assert fixture["api"].last_patch["spec"]["replicas"] == 30
    annotations = fixture["api"].last_patch["metadata"]["annotations"]
    assert annotations[
        "agent-control-plane.openai.com/action-hash"
    ] == fixture["action"].action_hash
    assert annotations[
        "agent-control-plane.openai.com/transition-hash"
    ] == fixture["transition"].transition_hash
    assert annotations[
        "agent-control-plane.openai.com/operation-id"
    ] == receipt.operation_id


def test_generation_drift_is_rejected_before_patch():
    fixture = build_transition()
    fixture["api"].deployment["metadata"]["generation"] = 8

    with pytest.raises(
        ProtocolViolation,
        match="resource generation changed before execution",
    ):
        execute(fixture)

    assert fixture["api"].patch_calls == 0


def test_live_before_state_drift_is_rejected_before_patch():
    fixture = build_transition()
    fixture["api"].deployment["spec"]["replicas"] = 25

    with pytest.raises(
        ProtocolViolation,
        match="live replicas do not match transition before state",
    ):
        execute(fixture)

    assert fixture["api"].patch_calls == 0


def test_lost_ack_reconciles_by_action_ownership():
    fixture = build_transition(LostAckDeploymentApi())

    receipt = execute(fixture)

    assert receipt.changed is True
    assert receipt.verified_after_uncertain_mutation is True
    assert fixture["api"].deployment["spec"]["replicas"] == 30
    assert fixture["api"].patch_calls == 1


def test_lost_ack_does_not_claim_another_controllers_mutation():
    fixture = build_transition(LostAckOwnedByOtherApi())

    with pytest.raises(RuntimeMutationOwnershipUncertain):
        execute(fixture)


def test_same_action_replay_does_not_repeat_side_effect():
    fixture = build_transition()

    first = execute(fixture)
    second = execute(fixture)

    assert first.changed is True
    assert second.changed is False
    assert second.replayed is True
    assert fixture["api"].patch_calls == 1


def test_independent_observation_reads_deployment_pods_and_events():
    fixture = build_transition()
    execute(fixture)
    fixture["api"].deployment["status"] = {
        "replicas": 30,
        "readyReplicas": 30,
        "availableReplicas": 30,
    }
    fixture["api"].pods = [
        {
            "metadata": {
                "name": f"payment-api-{index}",
                "uid": f"pod-{index}",
            },
            "status": {"phase": "Running"},
        }
        for index in range(30)
    ]

    observation = observe(fixture)

    assert observation.deployment["status"]["readyReplicas"] == 30
    assert len(observation.pods) == 30
    assert len(observation.events) == 1
    assert len(observation.evidence_items) == 3
    assert fixture["api"].pod_calls == 1
    assert fixture["api"].event_calls == 1
    observation.evidence_bundle.verify(
        item_lookup={
            item.evidence_id: item
            for item in observation.evidence_items
        }
    )


def test_full_scale_observe_verify_path_succeeds():
    fixture = build_transition()
    execute(fixture)
    fixture["api"].deployment["status"] = {
        "replicas": 30,
        "readyReplicas": 30,
        "availableReplicas": 30,
    }

    result = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=observe(fixture),
        safety_metrics={
            "error_rate": 0.002,
            "p95_ms": 210,
        },
        checked_at=NOW + timedelta(seconds=3),
    )

    assert result.status is VerificationStatus.SUCCEEDED
    assert all(item.passed is True for item in result.desired)
    assert all(item.passed is True for item in result.safety)


def test_provider_ack_does_not_mean_transition_success():
    fixture = build_transition()
    receipt = execute(fixture)

    result = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=observe(fixture),
        safety_metrics={
            "error_rate": 0.002,
            "p95_ms": 210,
        },
        checked_at=NOW + timedelta(seconds=3),
    )

    assert receipt.changed is True
    assert fixture["api"].deployment["spec"]["replicas"] == 30
    assert fixture["api"].deployment["status"]["readyReplicas"] == 20
    assert result.status is VerificationStatus.DEGRADED


def test_safety_violation_is_invariant_violation():
    fixture = build_transition()
    execute(fixture)
    fixture["api"].deployment["status"]["readyReplicas"] = 30

    result = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=observe(fixture),
        safety_metrics={
            "error_rate": 0.20,
            "p95_ms": 210,
        },
        checked_at=NOW + timedelta(seconds=3),
    )

    assert result.status is VerificationStatus.INVARIANT_VIOLATION


def test_missing_safety_evidence_fails_closed_as_unknown():
    fixture = build_transition()
    execute(fixture)
    fixture["api"].deployment["status"]["readyReplicas"] = 30

    result = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=observe(fixture),
        safety_metrics={
            "error_rate": 0.002,
        },
        checked_at=NOW + timedelta(seconds=3),
    )

    assert result.status is VerificationStatus.UNKNOWN


def test_unreached_desired_state_times_out_after_deadline():
    fixture = build_transition()
    execute(fixture)

    result = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=observe(
            fixture,
            at=NOW + timedelta(seconds=301),
        ),
        safety_metrics={
            "error_rate": 0.002,
            "p95_ms": 210,
        },
        checked_at=NOW + timedelta(seconds=301),
    )

    assert result.status is VerificationStatus.TIMED_OUT


def test_nonzero_stabilization_window_is_unknown_in_single_snapshot_verifier():
    fixture = build_transition()
    contract = OutcomeContract.seal(
        desired_conditions=fixture["outcome"].desired_conditions,
        safety_conditions=fixture["outcome"].safety_conditions,
        stabilization_seconds=60,
        deadline_seconds=300,
    )
    transition = StateTransition.seal(
        transition_id="tr-stabilized",
        subject=fixture["resource"],
        expected_generation=7,
        before={"replicas": 20},
        desired={"replicas": 30},
        evidence_hash=fixture["evidence"].manifest_hash,
        outcome_contract_hash=contract.contract_hash,
        created_at=NOW,
    )

    result = verify_outcome(
        transition=transition,
        outcome_contract=contract,
        observation=observe(fixture),
        safety_metrics={
            "error_rate": 0.002,
            "p95_ms": 210,
        },
        checked_at=NOW + timedelta(seconds=3),
    )

    assert result.status is VerificationStatus.UNKNOWN
    assert "stabilization window" in result.reasons[0]
