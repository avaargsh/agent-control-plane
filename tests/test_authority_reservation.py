from datetime import timedelta

import pytest

from agent_control_plane.authority_reservation import (
    AuthorityReservationState,
    SQLiteAuthorityReservationStore,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.runtime_clients import (
    RuntimeMutationOwnershipUncertain,
)
from agent_control_plane.state_transition_protocol import (
    ExecutionLease,
    Principal,
    ProtocolViolation,
)
from agent_control_plane.work_context import SQLiteWorkContextStore
from context_testkit import (
    HUMAN,
    NOW,
    PROPOSER,
    build_context_bound_execution_v3,
    prepare_context_attempt_v3,
)
from kubernetes_testkit import (
    FakeDeploymentApi,
    LostAckDeploymentApi,
    LostAckOwnedByOtherApi,
)


class ActiveLeaseAuthority:
    def __init__(self, expected: ExecutionLease) -> None:
        self.expected = expected
        self.calls = 0

    def assert_active(self, lease: ExecutionLease, *, now):
        self.calls += 1
        assert lease == self.expected
        lease.assert_active(now)
        return object()


def _execute_bound(fixture, context, attempt, *, at=None):
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
        now=at or NOW + timedelta(seconds=6),
        operation_id=attempt.operation_id,
        context_binding=context,
    )


def test_active_reservation_blocks_authority_mutation_until_release(
    tmp_path,
):
    (
        fixture,
        store,
        _,
        authority,
        proposal,
        _,
    ) = build_context_bound_execution_v3(tmp_path)
    reservations = SQLiteAuthorityReservationStore(store.path)

    reservation = reservations.acquire(
        reservation_id="reservation-1",
        work_id=proposal.work_id,
        expected_authority_generation=authority.generation,
        expected_authority_hash=authority.authority_hash,
        proposal_hash=proposal.proposal_hash,
        operation_id="authority-test-op",
        execution_lease=fixture["lease"],
        now=NOW + timedelta(seconds=5),
    )

    current = store.get(proposal.work_id)
    with pytest.raises(
        ProtocolViolation,
        match="blocked by active execution reservation",
    ):
        store.record_progress(
            work_id=proposal.work_id,
            expected_version=current.version,
            actor=PROPOSER,
            updated_at=NOW + timedelta(seconds=6),
            state_patch={"phase": "must-wait"},
        )

    released = reservations.release(
        reservation,
        execution_lease=fixture["lease"],
        now=NOW + timedelta(seconds=6),
    )
    assert released.state is AuthorityReservationState.RELEASED

    updated = store.record_progress(
        work_id=proposal.work_id,
        expected_version=current.version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=7),
        state_patch={"phase": "after-execution"},
    )
    assert updated.version == current.version + 1


def test_reservation_rejects_stale_authority_generation(tmp_path):
    (
        fixture,
        store,
        _,
        authority,
        proposal,
        _,
    ) = build_context_bound_execution_v3(tmp_path)
    current = store.get(proposal.work_id)
    store.record_progress(
        work_id=proposal.work_id,
        expected_version=current.version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=5),
        state_patch={"phase": "new-authority"},
    )

    reservations = SQLiteAuthorityReservationStore(store.path)
    with pytest.raises(
        ProtocolViolation,
        match="reservation generation is stale",
    ):
        reservations.acquire(
            reservation_id="reservation-stale",
            work_id=proposal.work_id,
            expected_authority_generation=authority.generation,
            expected_authority_hash=authority.authority_hash,
            proposal_hash=proposal.proposal_hash,
            operation_id="authority-stale-op",
            execution_lease=fixture["lease"],
            now=NOW + timedelta(seconds=6),
        )


def test_higher_epoch_takeover_requires_durable_active_lease_authority(
    tmp_path,
):
    (
        fixture,
        store,
        _,
        authority,
        proposal,
        _,
    ) = build_context_bound_execution_v3(tmp_path)
    reservations = SQLiteAuthorityReservationStore(store.path)
    old = reservations.acquire(
        reservation_id="reservation-old",
        work_id=proposal.work_id,
        expected_authority_generation=authority.generation,
        expected_authority_hash=authority.authority_hash,
        proposal_hash=proposal.proposal_hash,
        operation_id="authority-test-op",
        execution_lease=fixture["lease"],
        now=NOW + timedelta(seconds=5),
    )
    newer_lease = ExecutionLease(
        lease_id="lease-payment-api-new-owner",
        resource_uid=fixture["lease"].resource_uid,
        holder=Principal(
            type="controller",
            subject="agent-control-plane/controller-b",
        ),
        epoch=fixture["lease"].epoch + 1,
        acquired_at=NOW + timedelta(seconds=6),
        expires_at=NOW + timedelta(minutes=10),
    )

    with pytest.raises(
        ProtocolViolation,
        match="requires durable active lease authority",
    ):
        reservations.acquire(
            reservation_id="reservation-new-unverified",
            work_id=proposal.work_id,
            expected_authority_generation=authority.generation,
            expected_authority_hash=authority.authority_hash,
            proposal_hash=proposal.proposal_hash,
            operation_id="authority-test-op-new",
            execution_lease=newer_lease,
            now=NOW + timedelta(seconds=6),
        )

    lease_authority = ActiveLeaseAuthority(newer_lease)
    newer = reservations.acquire(
        reservation_id="reservation-new",
        work_id=proposal.work_id,
        expected_authority_generation=authority.generation,
        expected_authority_hash=authority.authority_hash,
        proposal_hash=proposal.proposal_hash,
        operation_id="authority-test-op-new",
        execution_lease=newer_lease,
        now=NOW + timedelta(seconds=6),
        lease_authority=lease_authority,
    )

    assert lease_authority.calls == 1
    assert newer.lease_epoch == old.lease_epoch + 1
    with pytest.raises(
        ProtocolViolation,
        match="not ACTIVE: SUPERSEDED",
    ):
        reservations.assert_active(
            old,
            execution_lease=fixture["lease"],
            now=NOW + timedelta(seconds=7),
        )


def test_expired_lease_does_not_silently_unfreeze_authority(
    tmp_path,
):
    path = tmp_path / "expiring.db"
    clock = [NOW + timedelta(seconds=2)]
    store = SQLiteWorkContextStore(
        path,
        clock=lambda: clock[0],
    )
    created = store.create(
        work_id="expiring-work",
        namespace="repo/demo",
        goal="prove reservation expiry stays fail-closed",
        actor=HUMAN,
        created_at=NOW,
        state={"target": 30},
    )
    claimed = store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=PROPOSER,
        claimed_at=NOW + timedelta(seconds=1),
    )
    authority = store.get_authority_head(created.work_id)
    lease = ExecutionLease(
        lease_id="short-lease",
        resource_uid="uid-expiring",
        holder=Principal(type="controller", subject="controller-a"),
        epoch=1,
        acquired_at=NOW + timedelta(seconds=2),
        expires_at=NOW + timedelta(seconds=4),
    )
    reservations = SQLiteAuthorityReservationStore(path)
    reservation = reservations.acquire(
        reservation_id="reservation-expiring",
        work_id=created.work_id,
        expected_authority_generation=authority.generation,
        expected_authority_hash=authority.authority_hash,
        proposal_hash="sha256:" + "a" * 64,
        operation_id="authority-expiry-op",
        execution_lease=lease,
        now=NOW + timedelta(seconds=2),
    )
    assert reservation.state is AuthorityReservationState.ACTIVE

    clock[0] = NOW + timedelta(seconds=5)
    with pytest.raises(
        ProtocolViolation,
        match="blocked by active execution reservation",
    ):
        store.record_progress(
            work_id=created.work_id,
            expected_version=claimed.version,
            actor=PROPOSER,
            updated_at=NOW + timedelta(seconds=5),
            state_patch={"target": 40},
        )

    # Lease expiry prevents continued execution, but does not itself prove that
    # an in-flight provider request cannot still commit.
    with pytest.raises(ProtocolViolation, match="lease has expired"):
        reservations.assert_active(
            reservation,
            execution_lease=lease,
            now=NOW + timedelta(seconds=5),
        )


class AuthorityRaceApi(FakeDeploymentApi):
    def __init__(self) -> None:
        super().__init__()
        self.before_patch = None
        self.blocked_error: ProtocolViolation | None = None

    def patch_deployment(self, *, namespace, name, patch):
        if self.before_patch is not None:
            callback = self.before_patch
            self.before_patch = None
            try:
                callback()
            except ProtocolViolation as exc:
                self.blocked_error = exc
            else:
                raise AssertionError(
                    "authority mutation unexpectedly crossed reservation"
                )
        return super().patch_deployment(
            namespace=namespace,
            name=name,
            patch=patch,
        )


def test_provider_holds_reservation_until_terminal_journal_state(
    tmp_path,
):
    api = AuthorityRaceApi()
    (
        fixture,
        store,
        _,
        authority,
        proposal,
        context,
        journal,
        _,
        attempt,
    ) = prepare_context_attempt_v3(
        tmp_path,
        api=api,
    )

    def race_authority_mutation():
        current = store.get(proposal.work_id)
        store.record_progress(
            work_id=proposal.work_id,
            expected_version=current.version,
            actor=PROPOSER,
            updated_at=NOW + timedelta(seconds=6),
            state_patch={"phase": "raced-provider-patch"},
        )

    api.before_patch = race_authority_mutation
    receipt = _execute_bound(
        fixture,
        context,
        attempt,
        at=NOW + timedelta(seconds=5),
    )

    assert receipt.changed is True
    assert receipt.authority_reservation_hash
    assert api.blocked_error is not None
    assert "blocked by active execution reservation" in str(api.blocked_error)
    assert (
        api.last_patch["metadata"]["annotations"][
            "agent-control-plane.openai.com/authority-reservation-hash"
        ]
        == receipt.authority_reservation_hash
    )
    assert store.get_authority_head(proposal.work_id) == authority

    # Provider acknowledgement alone is not terminal. Authority remains
    # frozen until the journal records the outcome.
    current = store.get(proposal.work_id)
    with pytest.raises(
        ProtocolViolation,
        match="blocked by active execution reservation",
    ):
        store.record_progress(
            work_id=proposal.work_id,
            expected_version=current.version,
            actor=PROPOSER,
            updated_at=NOW + timedelta(seconds=6),
            state_patch={"phase": "before-journal-commit"},
        )

    committed = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=7),
        result={"status": "APPLIED"},
    )
    context.authority_reservation_store.release_after_terminal(
        committed.authority_reservation,
        now=NOW + timedelta(seconds=7),
    )

    updated = store.record_progress(
        work_id=proposal.work_id,
        expected_version=current.version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=8),
        state_patch={"phase": "after-journal-commit"},
    )
    assert updated.version == current.version + 1


def test_verified_lost_ack_stays_frozen_until_terminal_journal_state(
    tmp_path,
):
    api = LostAckDeploymentApi()
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
    ) = prepare_context_attempt_v3(
        tmp_path,
        api=api,
    )

    receipt = _execute_bound(
        fixture,
        context,
        attempt,
        at=NOW + timedelta(seconds=5),
    )
    assert receipt.verified_after_uncertain_mutation is True
    assert receipt.authority_reservation_hash

    current = store.get(proposal.work_id)
    with pytest.raises(
        ProtocolViolation,
        match="blocked by active execution reservation",
    ):
        store.record_progress(
            work_id=proposal.work_id,
            expected_version=current.version,
            actor=PROPOSER,
            updated_at=NOW + timedelta(seconds=6),
            state_patch={"phase": "before-terminal-proof"},
        )

    committed = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=7),
        result={"status": "APPLIED"},
    )
    context.authority_reservation_store.release_after_terminal(
        committed.authority_reservation,
        now=NOW + timedelta(seconds=7),
    )

    updated = store.record_progress(
        work_id=proposal.work_id,
        expected_version=current.version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=8),
        state_patch={"phase": "after-terminal-proof"},
    )
    assert updated.version == current.version + 1


def test_unresolved_mutation_ownership_keeps_authority_frozen(tmp_path):
    api = LostAckOwnedByOtherApi()
    (
        fixture,
        store,
        _,
        _,
        proposal,
        context,
        _,
        _,
        attempt,
    ) = prepare_context_attempt_v3(
        tmp_path,
        api=api,
    )

    with pytest.raises(RuntimeMutationOwnershipUncertain):
        _execute_bound(
            fixture,
            context,
            attempt,
            at=NOW + timedelta(seconds=5),
        )

    current = store.get(proposal.work_id)
    with pytest.raises(
        ProtocolViolation,
        match="blocked by active execution reservation",
    ):
        store.record_progress(
            work_id=proposal.work_id,
            expected_version=current.version,
            actor=PROPOSER,
            updated_at=NOW + timedelta(seconds=6),
            state_patch={"phase": "must-stay-frozen"},
        )
