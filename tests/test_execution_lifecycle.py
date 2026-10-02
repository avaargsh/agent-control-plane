from datetime import timedelta

import pytest

from agent_control_plane.authority_reservation import (
    AuthorityReservationState,
    SQLiteAuthorityReservationStore,
)
from agent_control_plane.context_transition import (
    build_execution_context_provenance,
)
from agent_control_plane.execution_journal import (
    ExecutionAttemptState,
    SQLiteExecutionJournal,
)
from agent_control_plane.execution_lifecycle import (
    KubernetesDeploymentExecutionCoordinator,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.provable_execution import (
    PlanAuthorizationBinding,
    PlanExecutionFence,
)
from agent_control_plane.runtime_clients import (
    RuntimeMutationOwnershipUncertain,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation
from context_testkit import (
    NOW,
    PROPOSER,
    build_context_bound_execution_v3,
)
from kubernetes_testkit import (
    LostAckOwnedByOtherApi,
)


class StepClock:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self):
        self.index += 1
        return NOW + timedelta(seconds=4 + self.index)


class FailOnceCommitJournal(SQLiteExecutionJournal):
    def __init__(self, path) -> None:
        super().__init__(path)
        self.fail_next_commit = True

    def commit(self, attempt, *, completed_at, result):
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("simulated crash after provider acknowledgement")
        return super().commit(
            attempt,
            completed_at=completed_at,
            result=result,
        )


def _coordinator(tmp_path, fixture, store, *, journal=None):
    journal = journal or SQLiteExecutionJournal(
        tmp_path / "execution-lifecycle.db"
    )
    reservations = SQLiteAuthorityReservationStore(store.path)
    coordinator = KubernetesDeploymentExecutionCoordinator(
        provider=KubernetesDeploymentScaleProvider(fixture["api"]),
        journal=journal,
        reservation_store=reservations,
        clock=StepClock(),
    )
    return coordinator, journal, reservations


def _execute(coordinator, fixture, context):
    return coordinator.execute(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        caller=fixture["holder"],
        context_binding=context,
    )


def test_coordinator_commits_then_releases_authority(tmp_path):
    (
        fixture,
        store,
        _,
        _,
        proposal,
        context,
    ) = build_context_bound_execution_v3(tmp_path)
    coordinator, journal, reservations = _coordinator(
        tmp_path,
        fixture,
        store,
    )

    result = _execute(coordinator, fixture, context)

    assert result.receipt.changed is True
    assert result.attempt.state is ExecutionAttemptState.COMMITTED
    assert result.attempt.plan_authorization_hash is not None
    assert result.attempt.plan_fence_hash is not None
    assert result.reservation is not None
    terminal = result.attempt.result["_authority_reservation"]
    assert (
        terminal["reservation_hash"]
        == result.reservation.reservation_hash
    )
    assert terminal["operation_id"] == result.attempt.operation_id

    durable = reservations.get(result.reservation.reservation_id)
    assert durable is not None
    assert durable.state is AuthorityReservationState.RELEASED

    recovered = journal.get(result.attempt.attempt_id)
    assert recovered == result.attempt

    current = store.get(proposal.work_id)
    updated = store.record_progress(
        work_id=proposal.work_id,
        expected_version=current.version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=20),
        state_patch={"phase": "after-terminal"},
    )
    assert updated.version == current.version + 1


def test_retry_after_ack_before_commit_reuses_attempt_and_reservation(
    tmp_path,
):
    (
        fixture,
        store,
        _,
        _,
        _,
        context,
    ) = build_context_bound_execution_v3(tmp_path)
    journal = FailOnceCommitJournal(
        tmp_path / "execution-lifecycle.db"
    )
    coordinator, _, reservations = _coordinator(
        tmp_path,
        fixture,
        store,
        journal=journal,
    )

    with pytest.raises(
        RuntimeError,
        match="simulated crash after provider acknowledgement",
    ):
        _execute(coordinator, fixture, context)

    open_attempt = journal.open_for_action(
        resource_uid=fixture["resource"].resource_uid,
        action_hash=fixture["action"].action_hash,
        authorization_hash=fixture["authorization"].authorization_hash,
    )
    assert open_attempt is not None
    assert open_attempt.state is ExecutionAttemptState.PREPARED
    first_binding = open_attempt.authority_reservation
    assert first_binding is not None
    first_reservation = reservations.get(
        first_binding["reservation_id"]
    )
    assert first_reservation is not None
    assert first_reservation.state is AuthorityReservationState.ACTIVE
    assert fixture["api"].patch_calls == 1

    result = _execute(coordinator, fixture, context)

    assert result.attempt.attempt_id == open_attempt.attempt_id
    assert result.attempt.operation_id == open_attempt.operation_id
    assert result.receipt.replayed is True
    assert fixture["api"].patch_calls == 1
    assert (
        result.attempt.authority_reservation_hash
        == open_attempt.authority_reservation_hash
    )
    durable = reservations.get(first_binding["reservation_id"])
    assert durable is not None
    assert durable.state is AuthorityReservationState.RELEASED


def test_uncertain_provider_ownership_leaves_prepared_and_frozen(
    tmp_path,
):
    api = LostAckOwnedByOtherApi()
    (
        fixture,
        store,
        _,
        _,
        proposal,
        context,
    ) = build_context_bound_execution_v3(
        tmp_path,
        api=api,
    )
    coordinator, journal, reservations = _coordinator(
        tmp_path,
        fixture,
        store,
    )

    with pytest.raises(RuntimeMutationOwnershipUncertain):
        _execute(coordinator, fixture, context)

    open_attempt = journal.open_for_action(
        resource_uid=fixture["resource"].resource_uid,
        action_hash=fixture["action"].action_hash,
        authorization_hash=fixture["authorization"].authorization_hash,
    )
    assert open_attempt is not None
    assert open_attempt.state is ExecutionAttemptState.PREPARED
    binding = open_attempt.authority_reservation
    assert binding is not None
    durable = reservations.get(binding["reservation_id"])
    assert durable is not None
    assert durable.state is AuthorityReservationState.ACTIVE

    current = store.get(proposal.work_id)
    with pytest.raises(
        ProtocolViolation,
        match="blocked by active execution reservation",
    ):
        store.record_progress(
            work_id=proposal.work_id,
            expected_version=current.version,
            actor=PROPOSER,
            updated_at=NOW + timedelta(seconds=20),
            state_patch={"phase": "must-remain-frozen"},
        )


def test_prepare_recovers_acquire_before_journal_bind_gap(tmp_path):
    (
        fixture,
        store,
        _,
        _,
        proposal,
        context,
    ) = build_context_bound_execution_v3(tmp_path)
    coordinator, journal, reservations = _coordinator(
        tmp_path,
        fixture,
        store,
    )

    # Simulate the first process persisting PREPARED and acquiring the
    # deterministic reservation, then dying before journal.bind_*().
    provenance = build_execution_context_provenance(proposal)
    plan = coordinator.provider.prepare_plan(
        transition=fixture["transition"],
        action=fixture["action"],
        observer=fixture["holder"],
        now=NOW + timedelta(seconds=4),
    )
    plan_authorization = PlanAuthorizationBinding.derive(
        plan=plan,
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
    )
    plan_fence = PlanExecutionFence.bind(
        plan=plan,
        authorization=plan_authorization,
        lease=fixture["lease"],
    )
    attempt = journal.prepare_plan(
        plan=plan,
        authorization=plan_authorization,
        fence=plan_fence,
        prepared_at=NOW + timedelta(seconds=5),
        context_provenance=provenance,
    )
    reservation_id = f"execution-attempt:{attempt.attempt_id}"
    orphaned = reservations.acquire(
        reservation_id=reservation_id,
        work_id=proposal.work_id,
        expected_authority_generation=proposal.authority_generation,
        expected_authority_hash=proposal.authority_hash,
        proposal_hash=proposal.proposal_hash,
        operation_id=attempt.operation_id,
        execution_lease=fixture["lease"],
        now=NOW + timedelta(seconds=6),
    )
    assert journal.get(attempt.attempt_id).authority_reservation is None

    recovered = coordinator.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        context_binding=context,
    )

    assert recovered.attempt.attempt_id == attempt.attempt_id
    assert recovered.attempt.operation_id == attempt.operation_id
    assert (
        recovered.attempt.plan_authorization_hash
        == plan_authorization.binding_hash
    )
    assert recovered.attempt.plan_fence_hash == plan_fence.fence_hash
    assert recovered.reservation == orphaned
    assert (
        recovered.attempt.authority_reservation_hash
        == orphaned.reservation_hash
    )
    assert (
        recovered.context_binding.authority_reservation
        == orphaned
    )
