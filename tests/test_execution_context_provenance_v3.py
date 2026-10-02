from datetime import timedelta

from agent_control_plane.execution_journal import (
    ExecutionAttemptState,
    SQLiteExecutionJournal,
)
from context_testkit import NOW, prepare_context_attempt_v3


def test_v3_provenance_persists_authority_generation(tmp_path):
    (
        _,
        _,
        _,
        authority,
        proposal,
        _,
        journal,
        provenance,
        attempt,
    ) = prepare_context_attempt_v3(tmp_path)

    assert provenance.provenance_version == "execution-context-provenance/v3"
    assert provenance.authority_generation == authority.generation
    assert provenance.authority_state_hash == authority.authority_state_hash
    assert provenance.authority_hash == authority.authority_hash
    assert provenance.context_revision == proposal.context_revision

    restarted = SQLiteExecutionJournal(journal.path)
    recovered = restarted.get(attempt.attempt_id)

    assert recovered is not None
    assert recovered.state is ExecutionAttemptState.PREPARED
    bound = recovered.context_provenance
    assert bound["provenance_version"] == "execution-context-provenance/v3"
    assert bound["authority_generation"] == authority.generation
    assert bound["authority_state_hash"] == authority.authority_state_hash
    assert bound["authority_hash"] == authority.authority_hash
    assert bound["context_revision"] == proposal.context_revision


def test_v3_terminal_receipt_keeps_original_authority_identity(tmp_path):
    (
        _,
        _,
        overlay_store,
        authority,
        proposal,
        _,
        journal,
        provenance,
        attempt,
    ) = prepare_context_attempt_v3(tmp_path)

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
    assert bound["authority_generation"] == authority.generation
    assert bound["authority_state_hash"] == authority.authority_state_hash
    assert bound["authority_hash"] == authority.authority_hash
    assert bound["context_revision"] == proposal.context_revision
    reservation = committed.result["_authority_reservation"]
    assert (
        reservation["reservation_hash"]
        == committed.authority_reservation_hash
    )
    assert reservation["proposal_hash"] == proposal.proposal_hash
    assert reservation["operation_id"] == committed.operation_id
    committed.verify()
