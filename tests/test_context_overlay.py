from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agent_control_plane.context_overlay import (
    ContextOverlay,
    SQLiteContextOverlayStore,
)
from agent_control_plane.context_transition import assert_proposal_fresh
from agent_control_plane.state_transition_protocol import (
    Principal,
    ProtocolViolation,
)
from agent_control_plane.work_context import SQLiteWorkContextStore
from context_testkit import (
    PROPOSER,
    build_context_bound_execution,
)


NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
HUMAN = Principal(type="human", subject="ben")
CLAUDE = Principal(type="agent", subject="claude-code")
CODEX = Principal(type="agent", subject="codex")


def _stores(tmp_path):
    path = tmp_path / "context.db"
    work = SQLiteWorkContextStore(path)
    overlay = SQLiteContextOverlayStore(path)
    created = work.create(
        work_id="context-overlay-1",
        namespace="repo/demo",
        goal="Share non-authoritative context across agents",
        actor=HUMAN,
        created_at=NOW,
        state={"target": "authoritative"},
    )
    claimed = work.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=CLAUDE,
        claimed_at=NOW + timedelta(seconds=1),
    )
    return work, overlay, claimed


def test_context_revision_advances_without_mutating_authority(tmp_path):
    work, overlay, authoritative = _stores(tmp_path)

    entry = overlay.append(
        work_id=authoritative.work_id,
        expected_revision=0,
        actor=CODEX,
        entry_type="note",
        payload={
            "summary": "Codex reviewed the auth module",
            "confidence": "medium",
        },
        created_at=NOW + timedelta(seconds=2),
    )

    current = work.get(authoritative.work_id)
    projected = overlay.get(work_id=authoritative.work_id)

    assert entry.revision == 1
    assert projected.context_revision == 1
    assert projected.authority_version == authoritative.version
    assert (
        projected.authority_snapshot_hash
        == authoritative.snapshot_hash
    )
    assert current == authoritative
    assert projected.recent_entries == (entry,)


def test_context_append_uses_independent_revision_cas(tmp_path):
    _, overlay, authoritative = _stores(tmp_path)

    overlay.append(
        work_id=authoritative.work_id,
        expected_revision=0,
        actor=CLAUDE,
        entry_type="note",
        payload={"message": "first"},
        created_at=NOW + timedelta(seconds=2),
    )

    with pytest.raises(
        ProtocolViolation,
        match="stale context revision",
    ):
        overlay.append(
            work_id=authoritative.work_id,
            expected_revision=0,
            actor=CODEX,
            entry_type="note",
            payload={"message": "stale second writer"},
            created_at=NOW + timedelta(seconds=3),
        )


def test_context_changes_are_hash_linked_and_replayable(tmp_path):
    _, overlay, authoritative = _stores(tmp_path)

    first = overlay.append(
        work_id=authoritative.work_id,
        expected_revision=0,
        actor=CLAUDE,
        entry_type="observation",
        payload={"tests": "passing"},
        created_at=NOW + timedelta(seconds=2),
    )
    second = overlay.append(
        work_id=authoritative.work_id,
        expected_revision=1,
        actor=CODEX,
        entry_type="review",
        payload={"review": "needs-docs"},
        created_at=NOW + timedelta(seconds=3),
    )

    changes = overlay.changes_since(
        work_id=authoritative.work_id,
        revision=0,
    )

    assert changes == (first, second)
    assert second.prior_entry_hash == first.entry_hash
    assert overlay.changes_since(
        work_id=authoritative.work_id,
        revision=2,
    ) == ()


def test_context_overlay_detects_entry_tampering(tmp_path):
    _, overlay, authoritative = _stores(tmp_path)
    entry = overlay.append(
        work_id=authoritative.work_id,
        expected_revision=0,
        actor=CLAUDE,
        entry_type="note",
        payload={"message": "original"},
        created_at=NOW + timedelta(seconds=2),
    )
    current = overlay.get(work_id=authoritative.work_id)
    tampered_entry = replace(
        entry,
        payload={"message": "tampered"},
    )

    with pytest.raises(
        ProtocolViolation,
        match="context entry digest mismatch",
    ):
        ContextOverlay.seal(
            work_id=current.work_id,
            authority_version=current.authority_version,
            authority_snapshot_hash=current.authority_snapshot_hash,
            context_revision=current.context_revision,
            head_hash=current.head_hash,
            recent_entries=(tampered_entry,),
        )


def test_context_only_revision_does_not_stale_authority_proposal(tmp_path):
    fixture, store, proposal, _ = build_context_bound_execution(tmp_path)
    overlay = SQLiteContextOverlayStore(store.path)
    before = store.get(proposal.work_id)

    overlay.append(
        work_id=proposal.work_id,
        expected_revision=0,
        actor=Principal(type="agent", subject="codex"),
        entry_type="note",
        payload={
            "message": "new review note after proposal",
            "transition": fixture["transition"].transition_id,
        },
        created_at=NOW + timedelta(seconds=10),
    )

    after = store.get(proposal.work_id)
    assert after.version == before.version
    assert after.snapshot_hash == before.snapshot_hash

    assert_proposal_fresh(
        proposal=proposal,
        store=store,
    )


def test_context_overlay_requires_existing_work(tmp_path):
    path = tmp_path / "context.db"
    SQLiteWorkContextStore(path)
    overlay = SQLiteContextOverlayStore(path)

    with pytest.raises(
        ProtocolViolation,
        match="work item does not exist",
    ):
        overlay.append(
            work_id="missing",
            expected_revision=0,
            actor=CLAUDE,
            entry_type="note",
            payload={"message": "orphan"},
            created_at=NOW,
        )
