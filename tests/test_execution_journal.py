import sqlite3
from datetime import timedelta

import pytest

from agent_control_plane.execution_journal import (
    ExecutionAttemptState,
    ReconcileStatus,
    SQLiteExecutionJournal,
    reconcile_deployment_attempt,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.provable_execution import TransitionPlan
from agent_control_plane.runtime_clients import (
    RuntimeMutationOwnershipUncertain,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation
from kubernetes_testkit import NOW, FakeDeploymentApi, build_transition


def _prepare(journal, fixture):
    return journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW,
    )


def _prepare_with_plan(journal, fixture):
    provider = KubernetesDeploymentScaleProvider(fixture["api"])
    plan = provider.prepare_plan(
        transition=fixture["transition"],
        action=fixture["action"],
        observer=fixture["holder"],
        now=NOW,
    )
    attempt = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW + timedelta(milliseconds=1),
        transition_plan=plan,
    )
    return attempt, plan


def _execute_with_attempt(fixture, attempt):
    plan = (
        TransitionPlan.from_mapping(attempt.transition_plan)
        if attempt.transition_plan is not None
        else None
    )
    return KubernetesDeploymentScaleProvider(fixture["api"]).execute(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        caller=fixture["holder"],
        now=NOW + timedelta(seconds=1),
        operation_id=attempt.operation_id,
        execution_plan=plan,
    )


def test_prepared_attempt_survives_restart_with_same_operation_id(tmp_path):
    path = tmp_path / "execution.db"
    fixture = build_transition()
    first = SQLiteExecutionJournal(path)
    attempt = _prepare(first, fixture)

    restarted = SQLiteExecutionJournal(path)
    recovered = restarted.get(attempt.attempt_id)
    open_attempt = restarted.open_for_action(
        resource_uid=fixture["resource"].resource_uid,
        action_hash=fixture["action"].action_hash,
        authorization_hash=fixture["authorization"].authorization_hash,
    )

    assert recovered is not None
    assert recovered.state is ExecutionAttemptState.PREPARED
    assert recovered.operation_id == attempt.operation_id
    assert open_attempt == recovered


def test_prepare_is_idempotent_while_same_action_is_open(tmp_path):
    fixture = build_transition()
    journal = SQLiteExecutionJournal(tmp_path / "execution.db")

    first = _prepare(journal, fixture)
    second = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW + timedelta(seconds=1),
    )

    assert second.attempt_id == first.attempt_id
    assert second.operation_id == first.operation_id


def test_transition_plan_is_durable_before_side_effect(tmp_path):
    fixture = build_transition()
    path = tmp_path / "execution-plan.db"
    journal = SQLiteExecutionJournal(path)

    attempt, plan = _prepare_with_plan(journal, fixture)

    assert attempt.state is ExecutionAttemptState.PREPARED
    assert attempt.transition_plan_hash == plan.plan_hash
    assert attempt.transition_plan is not None
    assert (
        attempt.transition_plan["observation_hash"]
        == plan.observation_hash
    )
    assert fixture["api"].patch_calls == 0

    restarted = SQLiteExecutionJournal(path)
    recovered = restarted.get(attempt.attempt_id)
    assert recovered is not None
    assert recovered.transition_plan_hash == plan.plan_hash
    assert TransitionPlan.from_mapping(
        recovered.transition_plan
    ).plan_hash == plan.plan_hash


def test_reconcile_requires_durable_plan_ownership(tmp_path):
    fixture = build_transition(FakeDeploymentApi())
    journal = SQLiteExecutionJournal(tmp_path / "execution-plan.db")
    attempt, plan = _prepare_with_plan(journal, fixture)

    receipt = _execute_with_attempt(fixture, attempt)
    assert receipt.plan_hash == plan.plan_hash

    result = reconcile_deployment_attempt(
        api=fixture["api"],
        journal=journal,
        attempt=journal.get(attempt.attempt_id),
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=2),
    )

    assert result.status is ReconcileStatus.APPLIED
    committed = journal.get(attempt.attempt_id)
    assert committed is not None
    assert committed.transition_plan_hash == plan.plan_hash
    assert (
        committed.result["_transition_plan"]["plan_hash"]
        == plan.plan_hash
    )
    assert committed.result["plan_hash"] == plan.plan_hash


def test_reconcile_rejects_matching_operation_with_wrong_plan_hash(
    tmp_path,
):
    fixture = build_transition(FakeDeploymentApi())
    journal = SQLiteExecutionJournal(tmp_path / "execution-plan.db")
    attempt, plan = _prepare_with_plan(journal, fixture)

    api = fixture["api"]
    api.deployment["spec"]["replicas"] = 30
    api.deployment["metadata"]["generation"] = 8
    api.deployment["metadata"]["resourceVersion"] = "101"
    api.deployment["metadata"]["annotations"] = {
        "agent-control-plane.openai.com/action-hash":
            fixture["action"].action_hash,
        "agent-control-plane.openai.com/transition-hash":
            fixture["transition"].transition_hash,
        "agent-control-plane.openai.com/operation-id":
            attempt.operation_id,
        "agent-control-plane.openai.com/plan-hash":
            "sha256:" + "f" * 64,
    }

    result = reconcile_deployment_attempt(
        api=api,
        journal=journal,
        attempt=attempt,
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=2),
    )

    assert result.status is ReconcileStatus.AMBIGUOUS
    unknown = journal.get(attempt.attempt_id)
    assert unknown is not None
    assert unknown.state is ExecutionAttemptState.UNKNOWN
    assert unknown.transition_plan_hash == plan.plan_hash
    assert unknown.result["expected_plan_hash"] == plan.plan_hash


def test_crash_after_side_effect_before_receipt_reconstructs_exact_attempt(
    tmp_path,
):
    path = tmp_path / "execution.db"
    fixture = build_transition(FakeDeploymentApi())
    journal = SQLiteExecutionJournal(path)
    attempt = _prepare(journal, fixture)

    receipt = _execute_with_attempt(fixture, attempt)

    # Simulate process death here: provider mutation committed, but no journal
    # COMMITTED write happened.
    assert receipt.changed is True
    assert (
        fixture["api"].deployment["metadata"]["annotations"][
            "agent-control-plane.openai.com/operation-id"
        ]
        == attempt.operation_id
    )
    assert journal.get(attempt.attempt_id).state is ExecutionAttemptState.PREPARED

    restarted = SQLiteExecutionJournal(path)
    result = reconcile_deployment_attempt(
        api=fixture["api"],
        journal=restarted,
        attempt=restarted.get(attempt.attempt_id),
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=2),
    )

    assert result.status is ReconcileStatus.APPLIED
    committed = restarted.get(attempt.attempt_id)
    assert committed.state is ExecutionAttemptState.COMMITTED
    assert committed.result["reconstructed_after_crash"] is True
    assert committed.result["operation_id"] == attempt.operation_id
    assert committed.result_hash is not None


def test_reconcile_proves_not_applied_and_aborts_attempt(tmp_path):
    fixture = build_transition(FakeDeploymentApi())
    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    attempt = _prepare(journal, fixture)

    result = reconcile_deployment_attempt(
        api=fixture["api"],
        journal=journal,
        attempt=attempt,
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=1),
    )

    assert result.status is ReconcileStatus.NOT_APPLIED
    assert (
        journal.get(attempt.attempt_id).state
        is ExecutionAttemptState.ABORTED
    )
    assert fixture["api"].deployment["spec"]["replicas"] == 20


def test_reconcile_marks_ambiguous_concurrent_mutation_unknown(tmp_path):
    api = FakeDeploymentApi()
    fixture = build_transition(api)
    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    attempt = _prepare(journal, fixture)

    api.deployment["spec"]["replicas"] = 30
    api.deployment["metadata"]["generation"] = 8
    api.deployment["metadata"]["resourceVersion"] = "101"
    api.deployment["metadata"]["annotations"] = {
        "agent-control-plane.openai.com/action-hash":
            fixture["action"].action_hash,
        "agent-control-plane.openai.com/transition-hash":
            fixture["transition"].transition_hash,
        "agent-control-plane.openai.com/operation-id":
            "another-execution-attempt",
    }

    result = reconcile_deployment_attempt(
        api=api,
        journal=journal,
        attempt=attempt,
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=1),
    )

    assert result.status is ReconcileStatus.AMBIGUOUS
    unknown = journal.get(attempt.attempt_id)
    assert unknown.state is ExecutionAttemptState.UNKNOWN
    assert (
        unknown.result["observed_operation_id"]
        == "another-execution-attempt"
    )


def test_exact_attempt_replay_rejects_same_action_owned_by_other_operation(
    tmp_path,
):
    api = FakeDeploymentApi()
    fixture = build_transition(api)
    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    attempt = _prepare(journal, fixture)

    first = KubernetesDeploymentScaleProvider(api).execute(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        caller=fixture["holder"],
        now=NOW + timedelta(seconds=1),
        operation_id="different-operation",
    )
    assert first.changed is True

    with pytest.raises(RuntimeMutationOwnershipUncertain):
        _execute_with_attempt(fixture, attempt)


def test_commit_is_idempotent_for_same_terminal_result(tmp_path):
    fixture = build_transition()
    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    attempt = _prepare(journal, fixture)
    result = {
        "status": "APPLIED",
        "operation_id": attempt.operation_id,
    }

    first = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=1),
        result=result,
    )
    second = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=2),
        result=result,
    )

    assert first.state is ExecutionAttemptState.COMMITTED
    assert second.state is ExecutionAttemptState.COMMITTED
    assert second.result_hash == first.result_hash


def test_open_attempt_must_reconcile_before_lease_rebinding(tmp_path):
    fixture = build_transition()
    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    _prepare(journal, fixture)

    newer_fence = fixture["fence"].__class__(
        resource_uid=fixture["fence"].resource_uid,
        desired_generation=fixture["fence"].desired_generation,
        lease_id="replacement-lease",
        lease_holder=fixture["fence"].lease_holder,
        lease_epoch=fixture["fence"].lease_epoch + 1,
        transition_hash=fixture["fence"].transition_hash,
        action_hash=fixture["fence"].action_hash,
        authorization_hash=fixture["fence"].authorization_hash,
        expires_at=fixture["fence"].expires_at,
    )

    with pytest.raises(
        ProtocolViolation,
        match="must be reconciled before lease rebinding",
    ):
        journal.prepare(
            transition=fixture["transition"],
            action=fixture["action"],
            authorization=fixture["authorization"],
            fence=newer_fence,
            prepared_at=NOW + timedelta(seconds=1),
        )


def test_committed_action_cannot_open_new_execution_attempt(tmp_path):
    fixture = build_transition()
    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    attempt = _prepare(journal, fixture)
    journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=1),
        result={"status": "APPLIED"},
    )

    with pytest.raises(
        ProtocolViolation,
        match="already committed",
    ):
        _prepare(journal, fixture)


def test_unknown_action_blocks_new_execution_attempt(tmp_path):
    fixture = build_transition()
    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    attempt = _prepare(journal, fixture)
    journal.mark_unknown(
        attempt,
        completed_at=NOW + timedelta(seconds=1),
        result={"status": "AMBIGUOUS"},
    )

    with pytest.raises(
        ProtocolViolation,
        match="UNKNOWN prior attempt",
    ):
        _prepare(journal, fixture)


def test_tampered_prepared_attempt_is_rejected_by_digest(tmp_path):
    path = tmp_path / "execution.db"
    fixture = build_transition()
    journal = SQLiteExecutionJournal(path)
    attempt = _prepare(journal, fixture)

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE execution_attempts
            SET operation_id = ?
            WHERE attempt_id = ?
            """,
            ("tampered-operation", attempt.attempt_id),
        )
        connection.commit()

    with pytest.raises(
        ProtocolViolation,
        match="execution attempt digest mismatch",
    ):
        journal.get(attempt.attempt_id)


def test_tampered_terminal_result_is_rejected_by_digest(tmp_path):
    path = tmp_path / "execution.db"
    fixture = build_transition()
    journal = SQLiteExecutionJournal(path)
    attempt = _prepare(journal, fixture)
    journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=1),
        result={"status": "APPLIED", "replicas": 30},
    )

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE execution_attempts
            SET result_json = ?
            WHERE attempt_id = ?
            """,
            ('{"replicas":40,"status":"APPLIED"}', attempt.attempt_id),
        )
        connection.commit()

    with pytest.raises(
        ProtocolViolation,
        match="execution result digest mismatch",
    ):
        journal.get(attempt.attempt_id)
