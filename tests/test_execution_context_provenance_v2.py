from datetime import timedelta

from agent_control_plane.execution_journal import (
    ExecutionAttemptState,
    SQLiteExecutionJournal,
)
from context_testkit import NOW, prepare_context_attempt_v2


def test_v2_provenance_persists_observed_context_overlay(tmp_path):
    (
        _,
        _,
        _,
        proposal,
        _,
        journal,
        provenance,
        attempt,
    ) = prepare_context_attempt_v2(tmp_path)

    assert provenance.provenance_version == "execution-context-provenance/v2"
    assert provenance.context_revision == proposal.context_revision
    assert provenance.context_head_hash == proposal.context_head_hash
    assert provenance.context_overlay_hash == proposal.context_overlay_hash

    restarted = SQLiteExecutionJournal(journal.path)
    recovered = restarted.get(attempt.attempt_id)

    assert recovered is not None
    assert recovered.state is ExecutionAttemptState.PREPARED
    assert (
        recovered.context_provenance["provenance_version"]
        == "execution-context-provenance/v2"
    )
    assert (
        recovered.context_provenance["context_revision"]
        == proposal.context_revision
    )
    assert (
        recovered.context_provenance["context_head_hash"]
        == proposal.context_head_hash
    )
    assert (
        recovered.context_provenance["context_overlay_hash"]
        == proposal.context_overlay_hash
    )


def test_v2_terminal_receipt_keeps_original_context_revision_after_drift(
    tmp_path,
):
    (
        _,
        _,
        overlay_store,
        proposal,
        _,
        journal,
        provenance,
        attempt,
    ) = prepare_context_attempt_v2(tmp_path)

    overlay_store.append(
        work_id=proposal.work_id,
        expected_revision=proposal.context_revision,
        actor=proposal.proposer,
        entry_type="note",
        payload={"message": "context moved after PREPARED"},
        created_at=NOW + timedelta(seconds=5),
    )

    committed = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=6),
        result={"status": "APPLIED"},
    )

    bound = committed.result["_execution_context_provenance"]
    assert bound["provenance_hash"] == provenance.provenance_hash
    assert bound["context_revision"] == proposal.context_revision
    assert bound["context_head_hash"] == proposal.context_head_hash
    assert bound["context_overlay_hash"] == proposal.context_overlay_hash
    committed.verify()
