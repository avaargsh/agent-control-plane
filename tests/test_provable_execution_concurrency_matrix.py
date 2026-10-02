from datetime import timedelta

import pytest

from agent_control_plane.authority_reservation import (
    AuthorityReservationState,
    SQLiteAuthorityReservationStore,
)
from agent_control_plane.execution_journal import (
    ExecutionAttemptState,
    ReconcileStatus,
    SQLiteExecutionJournal,
    reconcile_deployment_attempt,
)
from agent_control_plane.execution_lifecycle import (
    KubernetesDeploymentExecutionCoordinator,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.provable_execution import validate_plan_execution
from agent_control_plane.state_transition_protocol import (
    ExecutionLease,
    ProtocolViolation,
)
from context_testkit import (
    NOW,
    PROPOSER,
    build_context_bound_execution,
    build_context_bound_execution_v3,
)


class StepClock:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self):
        self.index += 1
        return NOW + timedelta(seconds=4 + self.index)


def _stack(tmp_path):
    (
        fixture,
        store,
        _,
        _,
        proposal,
        context,
    ) = build_context_bound_execution_v3(tmp_path)
    journal = SQLiteExecutionJournal(
        tmp_path / "concurrency-matrix-execution.db"
    )
    reservations = SQLiteAuthorityReservationStore(store.path)
    coordinator = KubernetesDeploymentExecutionCoordinator(
        provider=KubernetesDeploymentScaleProvider(fixture["api"]),
        journal=journal,
        reservation_store=reservations,
        clock=StepClock(),
    )
    return (
        fixture,
        store,
        proposal,
        context,
        journal,
        reservations,
        coordinator,
    )


def _prepare(coordinator, fixture, context):
    return coordinator.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        context_binding=context,
    )


def _execute(coordinator, fixture, context, *, active_lease=None):
    return coordinator.execute(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=active_lease or fixture["lease"],
        caller=fixture["holder"],
        context_binding=context,
    )


def test_provider_version_drift_after_prepared_aborts_as_not_applied(
    tmp_path,
):
    (
        fixture,
        _,
        _,
        context,
        journal,
        reservations,
        coordinator,
    ) = _stack(tmp_path)

    prepared = _prepare(coordinator, fixture, context)
    assert prepared.reservation is not None
    assert fixture["api"].patch_calls == 0

    # Unrelated provider write changes the CAS token but not the approved
    # before-state. Our stale plan must never PATCH through it.
    fixture["api"].deployment["metadata"]["resourceVersion"] = "101"

    with pytest.raises(
        ProtocolViolation,
        match="resource version changed before execution",
    ):
        _execute(coordinator, fixture, context)

    assert fixture["api"].patch_calls == 0

    result = reconcile_deployment_attempt(
        api=fixture["api"],
        journal=journal,
        attempt=journal.get(prepared.attempt.attempt_id),
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=20),
        authority_reservation_store=reservations,
    )

    assert result.status is ReconcileStatus.NOT_APPLIED
    terminal = journal.get(prepared.attempt.attempt_id)
    assert terminal is not None
    assert terminal.state is ExecutionAttemptState.ABORTED
    durable = reservations.get(prepared.reservation.reservation_id)
    assert durable is not None
    assert durable.state is AuthorityReservationState.RELEASED


def test_unrelated_writer_reaches_desired_state_but_cannot_be_claimed(
    tmp_path,
):
    (
        fixture,
        _,
        _,
        context,
        journal,
        reservations,
        coordinator,
    ) = _stack(tmp_path)

    prepared = _prepare(coordinator, fixture, context)
    assert prepared.reservation is not None

    # Another writer reaches replicas=30 without our operation/plan markers.
    fixture["api"].deployment["spec"]["replicas"] = 30
    fixture["api"].deployment["metadata"]["generation"] = 8
    fixture["api"].deployment["metadata"]["resourceVersion"] = "101"
    fixture["api"].deployment["metadata"]["annotations"] = {
        "external-writer": "true",
    }

    with pytest.raises(
        ProtocolViolation,
        match="live replicas do not match transition before state",
    ):
        _execute(coordinator, fixture, context)

    assert fixture["api"].patch_calls == 0

    result = reconcile_deployment_attempt(
        api=fixture["api"],
        journal=journal,
        attempt=journal.get(prepared.attempt.attempt_id),
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=20),
        authority_reservation_store=reservations,
    )

    assert result.status is ReconcileStatus.AMBIGUOUS
    unknown = journal.get(prepared.attempt.attempt_id)
    assert unknown is not None
    assert unknown.state is ExecutionAttemptState.UNKNOWN
    durable = reservations.get(prepared.reservation.reservation_id)
    assert durable is not None
    assert durable.state is AuthorityReservationState.ACTIVE


def test_authority_generation_drift_rejects_before_provider_side_effect(
    tmp_path,
):
    (
        fixture,
        store,
        _,
        _,
        proposal,
        context,
    ) = build_context_bound_execution_v3(tmp_path)

    current = store.get(proposal.work_id)
    store.record_progress(
        work_id=proposal.work_id,
        expected_version=current.version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=5),
        state_patch={"target_replicas": 40},
    )

    journal = SQLiteExecutionJournal(
        tmp_path / "authority-drift-execution.db"
    )
    reservations = SQLiteAuthorityReservationStore(store.path)
    coordinator = KubernetesDeploymentExecutionCoordinator(
        provider=KubernetesDeploymentScaleProvider(fixture["api"]),
        journal=journal,
        reservation_store=reservations,
        clock=StepClock(),
    )

    with pytest.raises(
        ProtocolViolation,
        match="authority generation is stale",
    ):
        _execute(coordinator, fixture, context)

    assert fixture["api"].patch_calls == 0
    latest = journal.latest_for_action(
        resource_uid=fixture["resource"].resource_uid,
        action_hash=fixture["action"].action_hash,
        authorization_hash=fixture["authorization"].authorization_hash,
    )
    assert latest is not None
    assert latest.state is ExecutionAttemptState.PREPARED
    assert latest.authority_reservation is None


def test_higher_lease_epoch_cannot_rebind_prepared_plan(tmp_path):
    (
        fixture,
        _,
        _,
        context,
        _,
        reservations,
        coordinator,
    ) = _stack(tmp_path)

    prepared = _prepare(coordinator, fixture, context)
    assert prepared.reservation is not None

    replacement = ExecutionLease(
        lease_id=fixture["lease"].lease_id,
        resource_uid=fixture["lease"].resource_uid,
        holder=fixture["lease"].holder,
        epoch=fixture["lease"].epoch + 1,
        acquired_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=10),
    )

    with pytest.raises(
        ProtocolViolation,
        match="open plan execution attempt fence does not match",
    ):
        _execute(
            coordinator,
            fixture,
            context,
            active_lease=replacement,
        )

    assert fixture["api"].patch_calls == 0
    durable = reservations.get(prepared.reservation.reservation_id)
    assert durable is not None
    assert durable.state is AuthorityReservationState.ACTIVE

    with pytest.raises(
        ProtocolViolation,
        match="stale plan execution lease epoch",
    ):
        validate_plan_execution(
            plan=prepared.plan,
            authorization=prepared.plan_authorization,
            fence=prepared.plan_fence,
            active_lease=replacement,
            caller=replacement.holder,
            now=NOW + timedelta(seconds=2),
        )


def test_expired_signed_approval_cannot_mutate_provider(tmp_path):
    fixture, _, _, context = build_context_bound_execution(tmp_path)
    provider = KubernetesDeploymentScaleProvider(fixture["api"])

    with pytest.raises(
        ProtocolViolation,
        match="transition approval expired",
    ):
        provider.execute_context_bound(
            transition=fixture["transition"],
            evidence=fixture["evidence"],
            outcome_contract=fixture["outcome"],
            action=fixture["action"],
            authorization=fixture["authorization"],
            fence=fixture["fence"],
            active_lease=fixture["lease"],
            caller=fixture["holder"],
            now=NOW + timedelta(minutes=6),
            context_binding=context,
        )

    assert fixture["api"].patch_calls == 0


@pytest.mark.parametrize(
    "mutation",
    [
        {"metadata": {"resourceVersion": "101"}},
        {
            "metadata": {
                "generation": 8,
                "resourceVersion": "101",
            },
            "spec": {"replicas": 30},
        },
    ],
)
def test_prepared_plan_never_blindly_overwrites_concurrent_provider_state(
    tmp_path,
    mutation,
):
    (
        fixture,
        _,
        _,
        context,
        _,
        _,
        coordinator,
    ) = _stack(tmp_path)

    _prepare(coordinator, fixture, context)

    metadata = mutation.get("metadata", {})
    fixture["api"].deployment["metadata"].update(metadata)
    spec = mutation.get("spec", {})
    fixture["api"].deployment["spec"].update(spec)

    with pytest.raises(ProtocolViolation):
        _execute(coordinator, fixture, context)

    assert fixture["api"].patch_calls == 0
