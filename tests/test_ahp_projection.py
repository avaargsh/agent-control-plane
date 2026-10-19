from dataclasses import FrozenInstanceError

import pytest

from agent_control_plane.ahp_projection import (
    AhpProjection,
    AuthoritativeEvent,
    ChannelKey,
    ChannelKind,
    ProjectionRejected,
)


def key(tenant="team-a", kind=ChannelKind.SESSION):
    return ChannelKey(tenant, "session-1", kind, "resource-1")


def event(revision=1, *, tenant="team-a", kind=ChannelKind.SESSION, **changes):
    return AuthoritativeEvent(
        key(tenant, kind), revision, f"journal/{tenant}/{revision}", changes or {"status": "READY"}
    )


def verified(_event):
    return True


def authorized(channel):
    return channel.tenant_id == "team-a"


def test_verified_update_uses_global_sequence_and_per_channel_revision():
    projection = AhpProjection()
    one = projection.ingest(event(), verify=verified)
    two = projection.ingest(event(1, kind=ChannelKind.APPROVAL, status="PENDING"), verify=verified)
    three = projection.ingest(event(2, status="RUNNING"), verify=verified)
    assert [one.server_seq, two.server_seq, three.server_seq] == [1, 2, 3]
    out = projection.reconnect(last_seen_server_seq=1, keys=(key(),), authorize=authorized)
    assert out.mode == "replay"
    assert [(a.server_seq, a.event.revision) for a in out.actions] == [(3, 2)]


def test_gap_returns_snapshot_of_authorized_channels():
    projection = AhpProjection(replay_capacity=1)
    projection.ingest(event(), verify=verified)
    projection.ingest(event(2, status="RUNNING"), verify=verified)
    out = projection.reconnect(last_seen_server_seq=0, keys=(key(),), authorize=authorized)
    assert out.mode == "snapshot" and out.server_seq == 2
    assert out.snapshots[0].state == {"status": "RUNNING"}
    assert out.snapshots[0].revision == 2


def test_idle_reconnect_returns_empty_replay_not_snapshot():
    projection = AhpProjection()
    projection.ingest(event(), verify=verified)
    out = projection.reconnect(last_seen_server_seq=1, keys=(key(),), authorize=authorized)
    assert out.mode == "replay" and not out.actions


def test_unverified_provider_data_never_changes_state():
    projection = AhpProjection()
    with pytest.raises(PermissionError):
        projection.ingest(event(), verify=lambda _: False)
    assert projection.server_seq == 0


def test_duplicate_exact_is_idempotent_but_conflict_is_rejected():
    projection = AhpProjection()
    projection.ingest(event(), verify=verified)
    assert projection.ingest(event(), verify=verified).server_seq == 1
    assert projection.server_seq == 1
    projection.ingest(event(1, kind=ChannelKind.APPROVAL, status="PENDING"), verify=verified)
    assert projection.ingest(event(), verify=verified).server_seq == 1
    assert projection.server_seq == 2
    with pytest.raises(ProjectionRejected, match="conflicting"):
        projection.ingest(event(status="FAILED"), verify=verified)


def test_stale_and_skipped_source_revisions_fail_closed():
    projection = AhpProjection()
    projection.ingest(event(), verify=verified)
    for rev in (0, 3):
        with pytest.raises(ProjectionRejected):
            projection.ingest(event(rev, status="FAILED"), verify=verified)
    assert projection.server_seq == 1


def test_tenant_authorization_must_be_checked_on_reconnect():
    projection = AhpProjection()
    projection.ingest(event(), verify=verified)
    projection.ingest(event(tenant="team-b"), verify=verified)
    with pytest.raises(PermissionError):
        projection.reconnect(last_seen_server_seq=0, keys=(key("team-b"),), authorize=authorized)
    with pytest.raises(PermissionError):
        projection.reconnect(last_seen_server_seq=0, keys=(key(),), authorize=lambda _: False)


def test_future_cursor_is_rejected():
    projection = AhpProjection()
    projection.ingest(event(), verify=verified)
    with pytest.raises(ProjectionRejected):
        projection.reconnect(last_seen_server_seq=2, keys=(key(),), authorize=authorized)


def test_client_cannot_submit_arbitrary_status_or_mutate_authority():
    projection = AhpProjection()
    for changes in ({"status": "APPROVED"}, {"lease_epoch": "42"}, {"status": "UNKNOWN"}):
        with pytest.raises(ProjectionRejected):
            projection.ingest(event(**changes), verify=verified)
    assert projection.server_seq == 0


def test_execution_verified_requires_independent_proof_reference():
    projection = AhpProjection()
    with pytest.raises(ProjectionRejected, match="proof_ref"):
        projection.ingest(event(kind=ChannelKind.EXECUTION, status="VERIFIED"), verify=verified)
    assert projection.server_seq == 0
    projection.ingest(event(kind=ChannelKind.EXECUTION, status="VERIFIED", proof_ref="proof/hash"), verify=verified)


def test_mutating_returned_state_does_not_change_projection():
    projection = AhpProjection()
    original = event()
    projection.ingest(original, verify=verified)
    snapshot = projection.reconnect(last_seen_server_seq=0, keys=(key(),), authorize=authorized)
    # This is a replay (the first message fits). Mutating an action is blocked by dataclass.
    with pytest.raises(FrozenInstanceError):
        snapshot.actions[0].server_seq = 999
    projection.ingest(event(2, status="RUNNING"), verify=verified)
    result = projection.reconnect(last_seen_server_seq=0, keys=(key(),), authorize=authorized)
    assert result.actions[-1].event.changes == {"status": "RUNNING"}
