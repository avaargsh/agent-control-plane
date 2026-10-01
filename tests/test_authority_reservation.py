from datetime import timedelta

import pytest

from agent_control_plane.authority_reservation import (
    AuthorityReservationState,
    SQLiteAuthorityReservationStore,
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
            execution_lease=fixture["lease"],
            now=NOW + timedelta(seconds=6),
        )


def test_higher_execution_lease_epoch_supersedes_old_reservation(tmp_path):
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

    newer = reservations.acquire(
        reservation_id="reservation-new",
        work_id=proposal.work_id,
        expected_authority_generation=authority.generation,
        expected_authority_hash=authority.authority_hash,
        execution_lease=newer_lease,
        now=NOW + timedelta(seconds=6),
    )

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


def test_expired_reservation_no_longer_blocks_authority_mutation(
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
        goal="prove reservation expiry",
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
        execution_lease=lease,
        now=NOW + timedelta(seconds=2),
    )
    assert reservation.state is AuthorityReservationState.ACTIVE

    clock[0] = NOW + timedelta(seconds=5)
    updated = store.record_progress(
        work_id=created.work_id,
        expected_version=claimed.version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=5),
        state_patch={"target": 40},
    )
    assert updated.version == claimed.version + 1
