from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_control_plane.runtime_clients import RuntimeMutationUncertain
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
    ResourceIdentity,
    StateTransition,
)


NOW = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)


class FakeDeploymentApi:
    def __init__(self) -> None:
        self.patch_calls = 0
        self.pod_calls = 0
        self.event_calls = 0
        self.last_patch: dict[str, Any] | None = None
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
        raise RuntimeMutationUncertain(
            "connection reset after ambiguous commit"
        )


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
