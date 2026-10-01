from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

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
    canonical_digest,
    validate_execution,
)


NOW = datetime(2026, 10, 1, 3, 30, tzinfo=timezone.utc)


def protocol_fixture():
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
    evidence_item = EvidenceItem.capture(
        evidence_id="k8s-deployment-001",
        evidence_type="kubernetes_resource",
        source="kubernetes://prod/deployment/payment-api",
        collected_at=NOW,
        collector_name="kubernetes-observer",
        collector_version="v1.2.0",
        principal=collector,
        query="get deployment/payment-api",
        payload={
            "metadata": {"generation": 7},
            "spec": {"replicas": 20},
            "status": {"readyReplicas": 20},
        },
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
                expression="http_5xx_ratio",
                comparator="lt",
                expected=0.01,
            ),
        ),
        stabilization_seconds=60,
        deadline_seconds=300,
    )
    transition = StateTransition.seal(
        transition_id="tr-scale-payment-001",
        subject=resource,
        expected_generation=7,
        before={"replicas": 20},
        desired={"replicas": 30},
        evidence_hash=evidence.manifest_hash,
        outcome_contract_hash=outcome.contract_hash,
        created_at=NOW,
    )
    action = ActionIntent.seal(
        action_id="act-scale-payment-001",
        transition_hash=transition.transition_hash,
        provider="kubernetes",
        operation="scale_deployment",
        parameters={"replicas": 30},
    )
    authorized_principal = Principal(
        type="agent",
        subject="autoscaler-agent",
    )
    authorization = AuthorizationBinding.seal(
        transition=transition,
        action=action,
        policy_version="opa:scale-policy@sha256:1234",
        policy_decision_hash="sha256:" + "a" * 64,
        approval_hash="sha256:" + "b" * 64,
        principal=authorized_principal,
        expires_at=NOW + timedelta(minutes=5),
    )
    holder = Principal(
        type="controller",
        subject="agent-control-plane/controller-a",
    )
    lease = ExecutionLease(
        lease_id="lease-payment-api-001",
        resource_uid=resource.resource_uid,
        holder=holder,
        epoch=11,
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
        "resource": resource,
        "evidence_item": evidence_item,
        "evidence": evidence,
        "outcome": outcome,
        "transition": transition,
        "action": action,
        "authorization": authorization,
        "lease": lease,
        "holder": holder,
        "fence": fence,
    }


def validate_fixture(fixture, **overrides):
    args = {
        "transition": fixture["transition"],
        "evidence": fixture["evidence"],
        "outcome_contract": fixture["outcome"],
        "action": fixture["action"],
        "authorization": fixture["authorization"],
        "fence": fixture["fence"],
        "active_lease": fixture["lease"],
        "current_generation": 7,
        "caller": fixture["holder"],
        "now": NOW + timedelta(seconds=1),
    }
    args.update(overrides)
    validate_execution(**args)


def test_valid_transition_is_authorized_at_provider_edge():
    fixture = protocol_fixture()

    validate_fixture(fixture)


def test_evidence_capture_detaches_from_mutating_source():
    payload = {
        "metadata": {"generation": 7},
        "spec": {"replicas": 20},
    }
    fixture = protocol_fixture()
    item = EvidenceItem.capture(
        evidence_id="detached",
        evidence_type="kubernetes_resource",
        source="kubernetes://prod/deployment/payment-api",
        collected_at=NOW,
        collector_name="collector",
        collector_version="v1",
        principal=Principal(type="service_account", subject="collector"),
        payload=payload,
    )

    payload["spec"]["replicas"] = 999

    item.verify()
    assert item.payload["spec"]["replicas"] == 20
    fixture["evidence"].verify(
        item_lookup={
            fixture["evidence_item"].evidence_id:
                fixture["evidence_item"],
        }
    )


def test_tampered_evidence_payload_is_rejected():
    fixture = protocol_fixture()
    item = fixture["evidence_item"]
    object.__setattr__(
        item,
        "payload",
        {
            "metadata": {"generation": 7},
            "spec": {"replicas": 999},
        },
    )

    with pytest.raises(
        ProtocolViolation,
        match="evidence payload digest mismatch",
    ):
        fixture["evidence"].verify(
            item_lookup={item.evidence_id: item}
        )


def test_missing_evidence_item_is_rejected():
    fixture = protocol_fixture()

    with pytest.raises(
        ProtocolViolation,
        match="evidence item missing",
    ):
        fixture["evidence"].verify(item_lookup={})


def test_tampered_evidence_manifest_is_rejected():
    fixture = protocol_fixture()
    evidence = fixture["evidence"]
    object.__setattr__(evidence, "generation", 8)

    with pytest.raises(
        ProtocolViolation,
        match="evidence manifest digest mismatch",
    ):
        evidence.verify()


def test_action_modified_after_approval_is_rejected():
    fixture = protocol_fixture()
    changed_action = ActionIntent.seal(
        action_id=fixture["action"].action_id,
        transition_hash=fixture["transition"].transition_hash,
        provider="kubernetes",
        operation="scale_deployment",
        parameters={"replicas": 40},
    )

    with pytest.raises(
        ProtocolViolation,
        match="authorization action binding mismatch",
    ):
        validate_fixture(fixture, action=changed_action)


def test_resealed_transition_modified_after_approval_is_rejected():
    fixture = protocol_fixture()
    changed_transition = StateTransition.seal(
        transition_id=fixture["transition"].transition_id,
        subject=fixture["resource"],
        expected_generation=7,
        before={"replicas": 20},
        desired={"replicas": 40},
        evidence_hash=fixture["evidence"].manifest_hash,
        outcome_contract_hash=fixture["outcome"].contract_hash,
        created_at=NOW,
    )
    changed_action = ActionIntent.seal(
        action_id=fixture["action"].action_id,
        transition_hash=changed_transition.transition_hash,
        provider="kubernetes",
        operation="scale_deployment",
        parameters={"replicas": 40},
    )

    with pytest.raises(
        ProtocolViolation,
        match="authorization transition binding mismatch",
    ):
        validate_fixture(
            fixture,
            transition=changed_transition,
            action=changed_action,
        )


def test_generation_change_before_execute_is_rejected():
    fixture = protocol_fixture()

    with pytest.raises(
        ProtocolViolation,
        match="resource generation changed before execution",
    ):
        validate_fixture(fixture, current_generation=8)


def test_stale_controller_epoch_is_rejected():
    fixture = protocol_fixture()
    newer_lease = ExecutionLease(
        lease_id=fixture["lease"].lease_id,
        resource_uid=fixture["resource"].resource_uid,
        holder=fixture["holder"],
        epoch=12,
        acquired_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )

    with pytest.raises(
        ProtocolViolation,
        match="stale execution lease epoch",
    ):
        validate_fixture(fixture, active_lease=newer_lease)


def test_replaced_lease_identity_is_rejected():
    fixture = protocol_fixture()
    newer_lease = ExecutionLease(
        lease_id="lease-payment-api-002",
        resource_uid=fixture["resource"].resource_uid,
        holder=fixture["holder"],
        epoch=12,
        acquired_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )

    with pytest.raises(
        ProtocolViolation,
        match="stale execution lease id",
    ):
        validate_fixture(fixture, active_lease=newer_lease)


def test_non_holder_caller_is_rejected():
    fixture = protocol_fixture()
    stale_controller = Principal(
        type="controller",
        subject="agent-control-plane/controller-old",
    )

    with pytest.raises(
        ProtocolViolation,
        match="caller does not hold execution lease",
    ):
        validate_fixture(fixture, caller=stale_controller)


def test_authorization_expiry_fails_closed():
    fixture = protocol_fixture()

    with pytest.raises(
        ProtocolViolation,
        match="authorization expired",
    ):
        validate_fixture(
            fixture,
            now=NOW + timedelta(minutes=6),
        )


def test_tampered_outcome_contract_is_rejected():
    fixture = protocol_fixture()
    outcome = fixture["outcome"]
    object.__setattr__(outcome, "deadline_seconds", 900)

    with pytest.raises(
        ProtocolViolation,
        match="outcome contract digest mismatch",
    ):
        validate_fixture(fixture)


def test_canonical_digest_is_independent_of_mapping_key_order():
    left = {
        "desired": {"replicas": 30, "strategy": "scale"},
        "generation": 7,
    }
    right = {
        "generation": 7,
        "desired": {"strategy": "scale", "replicas": 30},
    }

    assert canonical_digest(left) == canonical_digest(right)


def test_outcome_contract_requires_explicit_desired_condition():
    with pytest.raises(
        ProtocolViolation,
        match="requires a desired condition",
    ):
        OutcomeContract.seal(
            desired_conditions=(),
            stabilization_seconds=60,
            deadline_seconds=300,
        )


def test_state_transition_rejects_noop():
    fixture = protocol_fixture()

    with pytest.raises(
        ProtocolViolation,
        match="cannot be a no-op",
    ):
        StateTransition.seal(
            transition_id="noop",
            subject=fixture["resource"],
            expected_generation=7,
            before={"replicas": 20},
            desired={"replicas": 20},
            evidence_hash=fixture["evidence"].manifest_hash,
            outcome_contract_hash=fixture["outcome"].contract_hash,
            created_at=NOW,
        )
