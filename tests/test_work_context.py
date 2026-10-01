from datetime import datetime, timedelta, timezone

import pytest

from agent_control_plane.state_transition_protocol import (
    Principal,
    ProtocolViolation,
)
from agent_control_plane.work_context import (
    ContextProjection,
    SQLiteWorkContextStore,
    WorkEvent,
    WorkStatus,
)


NOW = datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)
HUMAN = Principal(type="human", subject="ben")
CLAUDE = Principal(type="agent", subject="claude-code")
CODEX = Principal(type="agent", subject="codex")


def _created(store):
    return store.create(
        work_id="auth-refactor-17",
        namespace="repo/payment-api",
        goal="Refactor auth middleware without changing external behavior",
        actor=HUMAN,
        created_at=NOW,
        state={
            "branch": "feat/auth-refactor",
            "acceptance": ["tests pass", "no auth regression"],
        },
    )


def test_cross_agent_handoff_preserves_authoritative_work_state(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = _created(store)

    claimed = store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=CLAUDE,
        claimed_at=NOW + timedelta(seconds=1),
    )
    assert claimed.version == 2
    assert claimed.owner == CLAUDE
    assert claimed.status is WorkStatus.ACTIVE

    progressed = store.record_progress(
        work_id=created.work_id,
        expected_version=claimed.version,
        actor=CLAUDE,
        updated_at=NOW + timedelta(seconds=2),
        state_patch={
            "changed_files": ["src/auth.py"],
            "tests": "passing",
        },
        decisions=(
            "Keep token validation at the request boundary",
        ),
        evidence_refs=(
            "git:commit:abc123",
            "test:pytest:run-42",
        ),
    )

    handed_off = store.handoff(
        work_id=created.work_id,
        expected_version=progressed.version,
        from_agent=CLAUDE,
        to_agent=CODEX,
        handed_off_at=NOW + timedelta(seconds=3),
        reason="Implementation is complete; perform independent review",
        state_patch={"phase": "review"},
    )

    projection = store.project(
        work_id=created.work_id,
        consumer=CODEX,
    )
    projection.verify()

    assert handed_off.version == 4
    assert projection.work.version == 4
    assert projection.work.owner == CODEX
    assert projection.work.state["tests"] == "passing"
    assert projection.work.state["phase"] == "review"
    assert "git:commit:abc123" in projection.work.evidence_refs
    assert projection.recent_events[-1].operation == "handoff"
    assert projection.recent_events[-1].actor == CLAUDE


def test_stale_agent_cannot_claim_after_another_agent_wins(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = _created(store)

    store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=CLAUDE,
        claimed_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(
        ProtocolViolation,
        match="stale work version",
    ):
        store.claim(
            work_id=created.work_id,
            expected_version=created.version,
            agent=CODEX,
            claimed_at=NOW + timedelta(seconds=2),
        )


def test_stale_progress_write_is_rejected_after_handoff(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = _created(store)
    claimed = store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=CLAUDE,
        claimed_at=NOW + timedelta(seconds=1),
    )
    handed_off = store.handoff(
        work_id=created.work_id,
        expected_version=claimed.version,
        from_agent=CLAUDE,
        to_agent=CODEX,
        handed_off_at=NOW + timedelta(seconds=2),
        reason="Codex owns the review phase",
    )

    with pytest.raises(
        ProtocolViolation,
        match="stale work version",
    ):
        store.record_progress(
            work_id=created.work_id,
            expected_version=claimed.version,
            actor=CLAUDE,
            updated_at=NOW + timedelta(seconds=3),
            state_patch={"review": "claude continued from stale state"},
        )

    latest = store.get(created.work_id)
    assert latest.version == handed_off.version
    assert latest.owner == CODEX


def test_only_current_owner_can_record_progress(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = _created(store)
    claimed = store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=CLAUDE,
        claimed_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(
        ProtocolViolation,
        match="only the current work owner",
    ):
        store.record_progress(
            work_id=created.work_id,
            expected_version=claimed.version,
            actor=CODEX,
            updated_at=NOW + timedelta(seconds=2),
            state_patch={"status": "I should not be able to write"},
        )


def test_completion_requires_evidence_and_freezes_work(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = _created(store)
    claimed = store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=CLAUDE,
        claimed_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(
        ProtocolViolation,
        match="requires at least one evidence",
    ):
        store.complete(
            work_id=created.work_id,
            expected_version=claimed.version,
            agent=CLAUDE,
            completed_at=NOW + timedelta(seconds=2),
            evidence_refs=(),
        )

    completed = store.complete(
        work_id=created.work_id,
        expected_version=claimed.version,
        agent=CLAUDE,
        completed_at=NOW + timedelta(seconds=2),
        evidence_refs=("test:pytest:run-42",),
        state_patch={"result": "ready"},
    )
    assert completed.status is WorkStatus.DONE

    with pytest.raises(
        ProtocolViolation,
        match="completed work is immutable",
    ):
        store.record_progress(
            work_id=created.work_id,
            expected_version=completed.version,
            actor=CLAUDE,
            updated_at=NOW + timedelta(seconds=3),
            state_patch={"late": True},
        )


def test_changes_since_returns_versioned_append_only_history(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = _created(store)
    claimed = store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=CLAUDE,
        claimed_at=NOW + timedelta(seconds=1),
    )
    store.record_progress(
        work_id=created.work_id,
        expected_version=claimed.version,
        actor=CLAUDE,
        updated_at=NOW + timedelta(seconds=2),
        state_patch={"tests": "passing"},
    )

    events = store.changes_since(created.work_id, version=1)

    assert [event.operation for event in events] == [
        "claim",
        "progress",
    ]
    assert [event.to_version for event in events] == [2, 3]
    assert events[1].prior_snapshot_hash == events[0].snapshot_hash


def test_tampered_snapshot_fails_verification(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    snapshot = _created(store)

    object.__setattr__(snapshot, "goal", "tampered goal")

    with pytest.raises(
        ProtocolViolation,
        match="work snapshot digest mismatch",
    ):
        snapshot.verify()


def test_projection_rejects_broken_event_version_chain(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = _created(store)
    claimed = store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=CLAUDE,
        claimed_at=NOW + timedelta(seconds=1),
    )
    progressed = store.record_progress(
        work_id=created.work_id,
        expected_version=claimed.version,
        actor=CLAUDE,
        updated_at=NOW + timedelta(seconds=2),
        state_patch={"tests": "passing"},
    )
    projection = store.project(
        work_id=created.work_id,
        consumer=CLAUDE,
    )
    broken = list(projection.recent_events)
    last = broken[-1]
    broken[-1] = WorkEvent.seal(
        event_id=last.event_id,
        work_id=last.work_id,
        from_version=last.from_version - 1,
        to_version=last.to_version - 1,
        actor=last.actor,
        operation=last.operation,
        payload=last.payload,
        prior_snapshot_hash=last.prior_snapshot_hash,
        snapshot_hash=last.snapshot_hash,
        created_at=last.created_at,
    )

    with pytest.raises(
        ProtocolViolation,
        match="work event versions are not contiguous",
    ):
        ContextProjection.seal(
            consumer=CLAUDE,
            work=progressed,
            recent_events=broken,
        )


def test_projection_rejects_event_tail_not_bound_to_current_snapshot(
    tmp_path,
):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = _created(store)
    claimed = store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=CLAUDE,
        claimed_at=NOW + timedelta(seconds=1),
    )
    store.record_progress(
        work_id=created.work_id,
        expected_version=claimed.version,
        actor=CLAUDE,
        updated_at=NOW + timedelta(seconds=2),
        state_patch={"tests": "passing"},
    )
    current = store.get(created.work_id)
    events = store.changes_since(created.work_id, 0)

    with pytest.raises(
        ProtocolViolation,
        match="tail does not reach current snapshot version",
    ):
        current_projection = events[:-1]
        ContextProjection.seal(
            consumer=CLAUDE,
            work=current,
            recent_events=current_projection,
        )


def test_changes_since_rejects_version_newer_than_current(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = _created(store)

    with pytest.raises(
        ProtocolViolation,
        match="newer than current work version",
    ):
        store.changes_since(
            created.work_id,
            created.version + 1,
        )


def test_changes_since_with_snapshot_returns_one_consistent_tail(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = _created(store)
    claimed = store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=CLAUDE,
        claimed_at=NOW + timedelta(seconds=1),
    )
    progressed = store.record_progress(
        work_id=created.work_id,
        expected_version=claimed.version,
        actor=CLAUDE,
        updated_at=NOW + timedelta(seconds=2),
        state_patch={"phase": "review"},
    )

    snapshot, events = store.changes_since_with_snapshot(
        created.work_id,
        created.version,
    )

    assert snapshot == progressed
    assert events[-1].to_version == snapshot.version
    assert events[-1].snapshot_hash == snapshot.snapshot_hash
