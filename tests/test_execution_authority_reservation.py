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
    ReconcileStatus,
    SQLiteExecutionJournal,
    reconcile_deployment_attempt,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation
from context_testkit import (
    NOW,
    PROPOSER,
    build_context_bound_execution_v3,
    prepare_context_attempt_v3,
)


def _execute(fixture, context, attempt):
    return KubernetesDeploymentScaleProvider(
        fixture["api"],
    ).execute_context_bound(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        caller=fixture["holder"],
        now=NOW + timedelta(seconds=6),
        operation_id=attempt.operation_id,
        context_binding=context,
    )


def test_prepared_attempt_persists_reservation_binding_across_restart(
    tmp_path,
):
    (
        _,
        _,
        _,
        _,
        proposal,
        context,
        journal,
        _,
        attempt,
    ) = prepare_context_attempt_v3(tmp_path)

    restarted = SQLiteExecutionJournal(journal.path)
    recovered = restarted.get(attempt.attempt_id)

    assert recovered is not None
    assert recovered.state is ExecutionAttemptState.PREPARED
    assert (
        recovered.authority_reservation_hash
        == context.authority_reservation.reservation_hash
    )
    binding = recovered.authority_reservation
    assert binding["proposal_hash"] == proposal.proposal_hash
    assert binding["operation_id"] == attempt.operation_id
    assert binding["resource_uid"] == fixture_resource_uid(context, recovered)


def fixture_resource_uid(context, attempt):
    # Keep the assertion focused on the binding stored by the journal.
    assert context.authority_reservation is not None
    assert attempt.resource_uid == context.authority_reservation.resource_uid
    return attempt.resource_uid


def test_crash_reconcile_commits_exact_reservation_and_then_releases(
    tmp_path,
):
    (
        fixture,
        store,
        _,
        _,
        proposal,
        context,
        journal,
        _,
        attempt,
    ) = prepare_context_attempt_v3(tmp_path)

    receipt = _execute(fixture, context, attempt)
    assert receipt.changed is True
    assert (
        receipt.authority_reservation_hash
        == attempt.authority_reservation_hash
    )

    # Simulated crash: provider committed, journal is still PREPARED, and the
    # reservation remains ACTIVE so authority cannot drift before recovery.
    current = store.get(proposal.work_id)
    with pytest.raises(
        ProtocolViolation,
        match="blocked by active execution reservation",
    ):
        store.record_progress(
            work_id=proposal.work_id,
            expected_version=current.version,
            actor=PROPOSER,
            updated_at=NOW + timedelta(seconds=7),
            state_patch={"phase": "crash-window"},
        )

    restarted = SQLiteExecutionJournal(journal.path)
    reservations = SQLiteAuthorityReservationStore(store.path)
    result = reconcile_deployment_attempt(
        api=fixture["api"],
        journal=restarted,
        attempt=restarted.get(attempt.attempt_id),
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=8),
        authority_reservation_store=reservations,
    )

    assert result.status is ReconcileStatus.APPLIED
    committed = restarted.get(attempt.attempt_id)
    assert committed is not None
    assert committed.state is ExecutionAttemptState.COMMITTED
    terminal = committed.result["_authority_reservation"]
    assert terminal["reservation_hash"] == receipt.authority_reservation_hash
    assert terminal["proposal_hash"] == proposal.proposal_hash
    assert terminal["operation_id"] == attempt.operation_id

    durable = reservations.get(terminal["reservation_id"])
    assert durable is not None
    assert durable.state is AuthorityReservationState.RELEASED

    updated = store.record_progress(
        work_id=proposal.work_id,
        expected_version=current.version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=9),
        state_patch={"phase": "after-reconcile"},
    )
    assert updated.version == current.version + 1


def test_reconcile_rejects_provider_reservation_hash_swap(tmp_path):
    (
        fixture,
        store,
        _,
        _,
        proposal,
        context,
        journal,
        _,
        attempt,
    ) = prepare_context_attempt_v3(tmp_path)

    _execute(fixture, context, attempt)
    fixture["api"].deployment["metadata"]["annotations"][
        "agent-control-plane.openai.com/authority-reservation-hash"
    ] = "sha256:" + "f" * 64

    restarted = SQLiteExecutionJournal(journal.path)
    reservations = SQLiteAuthorityReservationStore(store.path)
    result = reconcile_deployment_attempt(
        api=fixture["api"],
        journal=restarted,
        attempt=restarted.get(attempt.attempt_id),
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=8),
        authority_reservation_store=reservations,
    )

    assert result.status is ReconcileStatus.AMBIGUOUS
    unknown = restarted.get(attempt.attempt_id)
    assert unknown is not None
    assert unknown.state is ExecutionAttemptState.UNKNOWN
    assert (
        unknown.result["expected_authority_reservation_hash"]
        == attempt.authority_reservation_hash
    )
    assert (
        unknown.result["observed_authority_reservation_hash"]
        == "sha256:" + "f" * 64
    )

    durable = reservations.get(
        context.authority_reservation.reservation_id
    )
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
            updated_at=NOW + timedelta(seconds=9),
            state_patch={"phase": "must-remain-frozen"},
        )


def test_v3_commit_without_durable_reservation_is_rejected(tmp_path):
    (
        fixture,
        _,
        _,
        _,
        proposal,
        _,
    ) = build_context_bound_execution_v3(tmp_path)
    journal = SQLiteExecutionJournal(tmp_path / "unbound-v3.db")
    attempt = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW + timedelta(seconds=4),
        context_provenance=build_execution_context_provenance(proposal),
    )

    with pytest.raises(
        ProtocolViolation,
        match="requires durable authority reservation",
    ):
        journal.commit(
            attempt,
            completed_at=NOW + timedelta(seconds=5),
            result={"status": "APPLIED"},
        )
