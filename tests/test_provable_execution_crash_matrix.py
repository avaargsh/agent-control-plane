from datetime import timedelta
from enum import Enum

import pytest

from agent_control_plane.authority_reservation import (
    AuthorityReservationState,
    SQLiteAuthorityReservationStore,
)
from agent_control_plane.execution_fencing import SQLiteExecutionLeaseStore
from agent_control_plane.execution_journal import (
    ExecutionAttemptState,
    SQLiteExecutionJournal,
)
from agent_control_plane.execution_lifecycle import (
    KubernetesDeploymentExecutionCoordinator,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentObserver,
    KubernetesDeploymentScaleProvider,
    VerificationStatus,
    verify_outcome,
)
from agent_control_plane.runtime_clients import (
    RuntimeMutationOwnershipUncertain,
)
from agent_control_plane.state_transition_protocol import Principal
from agent_control_plane.terminal_reservation_repair import (
    SQLiteTerminalReservationRepairStore,
    TerminalReservationRepair,
)
from context_testkit import NOW, build_context_bound_execution_v3
from kubernetes_testkit import (
    LostAckDeploymentApi,
    LostAckOwnedByOtherApi,
)


class CrashDisposition(str, Enum):
    SAFE_RETRY = "SAFE_RETRY"
    RECONCILE = "RECONCILE"
    UNKNOWN_BLOCKED = "UNKNOWN_BLOCKED"


EXPECTED_MATRIX = {
    "before_prepared": CrashDisposition.SAFE_RETRY,
    "after_prepared_before_provider": CrashDisposition.SAFE_RETRY,
    "provider_accepted_before_ack_owned": CrashDisposition.RECONCILE,
    "provider_accepted_before_ack_unknown": CrashDisposition.UNKNOWN_BLOCKED,
    "after_ack_before_committed": CrashDisposition.RECONCILE,
    "after_committed_before_release": CrashDisposition.RECONCILE,
    "during_verification": CrashDisposition.RECONCILE,
}


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
            raise RuntimeError("crash after provider ACK before COMMITTED")
        return super().commit(
            attempt,
            completed_at=completed_at,
            result=result,
        )


class FailOnceReleaseReservationStore(SQLiteAuthorityReservationStore):
    def __init__(self, path) -> None:
        super().__init__(path)
        self.fail_next_release = True

    def release_after_terminal(self, binding, *, now):
        if self.fail_next_release:
            self.fail_next_release = False
            raise RuntimeError(
                "crash after COMMITTED before reservation release"
            )
        return super().release_after_terminal(
            binding,
            now=now,
        )


def _stack(
    tmp_path,
    *,
    api=None,
    journal_cls=SQLiteExecutionJournal,
    reservation_cls=SQLiteAuthorityReservationStore,
):
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
    journal = journal_cls(tmp_path / "crash-matrix-execution.db")
    reservations = reservation_cls(store.path)
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


def _latest(journal, fixture):
    return journal.latest_for_action(
        resource_uid=fixture["resource"].resource_uid,
        action_hash=fixture["action"].action_hash,
        authorization_hash=fixture["authorization"].authorization_hash,
    )


def test_crash_matrix_has_every_required_boundary():
    assert EXPECTED_MATRIX == {
        "before_prepared": CrashDisposition.SAFE_RETRY,
        "after_prepared_before_provider": CrashDisposition.SAFE_RETRY,
        "provider_accepted_before_ack_owned": CrashDisposition.RECONCILE,
        "provider_accepted_before_ack_unknown":
            CrashDisposition.UNKNOWN_BLOCKED,
        "after_ack_before_committed": CrashDisposition.RECONCILE,
        "after_committed_before_release": CrashDisposition.RECONCILE,
        "during_verification": CrashDisposition.RECONCILE,
    }


def test_before_prepared_is_safe_retry(tmp_path):
    (
        fixture,
        _,
        _,
        context,
        journal,
        _,
        coordinator,
    ) = _stack(tmp_path)

    assert _latest(journal, fixture) is None
    assert fixture["api"].patch_calls == 0

    result = _execute(coordinator, fixture, context)

    assert result.attempt.state is ExecutionAttemptState.COMMITTED
    assert fixture["api"].patch_calls == 1
    assert EXPECTED_MATRIX["before_prepared"] is CrashDisposition.SAFE_RETRY


def test_after_prepared_before_provider_reuses_exact_attempt(tmp_path):
    (
        fixture,
        _,
        _,
        context,
        journal,
        reservations,
        coordinator,
    ) = _stack(tmp_path)

    prepared = coordinator.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        context_binding=context,
    )

    assert prepared.attempt.state is ExecutionAttemptState.PREPARED
    assert fixture["api"].patch_calls == 0
    assert prepared.reservation is not None
    durable = reservations.get(prepared.reservation.reservation_id)
    assert durable is not None
    assert durable.state is AuthorityReservationState.ACTIVE

    result = _execute(coordinator, fixture, context)

    assert result.attempt.attempt_id == prepared.attempt.attempt_id
    assert result.attempt.operation_id == prepared.attempt.operation_id
    assert result.attempt.state is ExecutionAttemptState.COMMITTED
    assert fixture["api"].patch_calls == 1
    assert (
        EXPECTED_MATRIX["after_prepared_before_provider"]
        is CrashDisposition.SAFE_RETRY
    )


def test_provider_accept_before_ack_is_reconciled_when_owned(tmp_path):
    api = LostAckDeploymentApi()
    (
        fixture,
        _,
        _,
        context,
        _,
        reservations,
        coordinator,
    ) = _stack(tmp_path, api=api)

    result = _execute(coordinator, fixture, context)

    assert result.receipt.verified_after_uncertain_mutation is True
    assert result.attempt.state is ExecutionAttemptState.COMMITTED
    assert fixture["api"].patch_calls == 1
    assert result.reservation is not None
    durable = reservations.get(result.reservation.reservation_id)
    assert durable is not None
    assert durable.state is AuthorityReservationState.RELEASED
    assert (
        EXPECTED_MATRIX["provider_accepted_before_ack_owned"]
        is CrashDisposition.RECONCILE
    )


def test_provider_accept_before_ack_unknown_stays_frozen(tmp_path):
    api = LostAckOwnedByOtherApi()
    (
        fixture,
        _,
        _,
        context,
        journal,
        reservations,
        coordinator,
    ) = _stack(tmp_path, api=api)

    with pytest.raises(RuntimeMutationOwnershipUncertain):
        _execute(coordinator, fixture, context)

    attempt = _latest(journal, fixture)
    assert attempt is not None
    assert attempt.state is ExecutionAttemptState.PREPARED
    assert attempt.authority_reservation is not None
    durable = reservations.get(
        str(attempt.authority_reservation["reservation_id"])
    )
    assert durable is not None
    assert durable.state is AuthorityReservationState.ACTIVE
    assert fixture["api"].patch_calls == 1
    assert (
        EXPECTED_MATRIX["provider_accepted_before_ack_unknown"]
        is CrashDisposition.UNKNOWN_BLOCKED
    )


def test_ack_before_committed_reconciles_without_repeating_side_effect(
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
    ) = _stack(
        tmp_path,
        journal_cls=FailOnceCommitJournal,
    )

    with pytest.raises(
        RuntimeError,
        match="after provider ACK before COMMITTED",
    ):
        _execute(coordinator, fixture, context)

    prepared = _latest(journal, fixture)
    assert prepared is not None
    assert prepared.state is ExecutionAttemptState.PREPARED
    assert fixture["api"].patch_calls == 1
    assert prepared.authority_reservation is not None
    durable = reservations.get(
        str(prepared.authority_reservation["reservation_id"])
    )
    assert durable is not None
    assert durable.state is AuthorityReservationState.ACTIVE

    result = _execute(coordinator, fixture, context)

    assert result.attempt.attempt_id == prepared.attempt_id
    assert result.attempt.operation_id == prepared.operation_id
    assert result.receipt.replayed is True
    assert result.attempt.state is ExecutionAttemptState.COMMITTED
    assert fixture["api"].patch_calls == 1
    assert (
        EXPECTED_MATRIX["after_ack_before_committed"]
        is CrashDisposition.RECONCILE
    )


def test_committed_before_release_repairs_only_from_terminal_proof(
    tmp_path,
):
    (
        fixture,
        store,
        _,
        context,
        journal,
        reservations,
        coordinator,
    ) = _stack(
        tmp_path,
        reservation_cls=FailOnceReleaseReservationStore,
    )

    with pytest.raises(
        RuntimeError,
        match="after COMMITTED before reservation release",
    ):
        _execute(coordinator, fixture, context)

    terminal = _latest(journal, fixture)
    assert terminal is not None
    assert terminal.state is ExecutionAttemptState.COMMITTED
    assert terminal.authority_reservation is not None
    reservation_id = str(
        terminal.authority_reservation["reservation_id"]
    )
    durable = reservations.get(reservation_id)
    assert durable is not None
    assert durable.state is AuthorityReservationState.ACTIVE

    repair = TerminalReservationRepair(
        journal=journal,
        reservation_store=reservations,
        repair_store=SQLiteTerminalReservationRepairStore(store.path),
        lease_lookup=SQLiteExecutionLeaseStore(
            tmp_path / "crash-matrix-leases.db"
        ),
    )
    evidence = repair.repair(
        terminal_attempt_id=terminal.attempt_id,
        actor=Principal(
            type="controller",
            subject="crash-matrix-repair",
        ),
        repaired_at=NOW + timedelta(seconds=30),
    )

    repaired = reservations.get(reservation_id)
    assert repaired is not None
    assert repaired.state is AuthorityReservationState.RELEASED
    assert evidence.terminal_attempt_id == terminal.attempt_id
    assert evidence.terminal_result_hash == terminal.result_hash
    assert fixture["api"].patch_calls == 1
    assert (
        EXPECTED_MATRIX["after_committed_before_release"]
        is CrashDisposition.RECONCILE
    )


def test_verification_pending_reobserves_without_reapplying_effect(
    tmp_path,
):
    (
        fixture,
        _,
        _,
        context,
        journal,
        _,
        coordinator,
    ) = _stack(tmp_path)

    result = _execute(coordinator, fixture, context)
    assert result.attempt.state is ExecutionAttemptState.COMMITTED
    assert fixture["api"].patch_calls == 1

    observer = KubernetesDeploymentObserver(fixture["api"])
    first_observation = observer.observe(
        resource=fixture["resource"],
        principal=fixture["collector"],
        observed_at=NOW + timedelta(seconds=20),
    )
    first = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=first_observation,
        safety_metrics={
            "error_rate": 0.002,
            "p95_ms": 210,
        },
        checked_at=NOW + timedelta(seconds=20),
    )

    assert first.status is VerificationStatus.DEGRADED
    assert journal.get(result.attempt.attempt_id).state is (
        ExecutionAttemptState.COMMITTED
    )
    assert fixture["api"].patch_calls == 1

    fixture["api"].deployment["status"]["readyReplicas"] = 30
    fixture["api"].deployment["status"]["availableReplicas"] = 30
    second_observation = observer.observe(
        resource=fixture["resource"],
        principal=fixture["collector"],
        observed_at=NOW + timedelta(seconds=30),
    )
    second = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=second_observation,
        safety_metrics={
            "error_rate": 0.002,
            "p95_ms": 210,
        },
        checked_at=NOW + timedelta(seconds=30),
    )

    assert second.status is VerificationStatus.SUCCEEDED
    assert fixture["api"].patch_calls == 1
    assert (
        EXPECTED_MATRIX["during_verification"]
        is CrashDisposition.RECONCILE
    )
