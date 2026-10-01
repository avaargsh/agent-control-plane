import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from agent_control_plane.execution_journal import (
    SQLiteExecutionJournal,
    reconcile_deployment_attempt,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentObserver,
    KubernetesDeploymentScaleProvider,
    VerificationStatus,
    verify_outcome,
)
from agent_control_plane.policy_replay import (
    DeploymentScalePolicy,
    TransitionPolicyInput,
    evaluate_policy,
)
from agent_control_plane.state_transition_protocol import (
    AuthorizationBinding,
    ExecutionFence,
    ExecutionLease,
    Principal,
    ProtocolViolation,
)
from agent_control_plane.transition_approval import (
    ApprovalDecision,
    ApprovalSigningKey,
    HMACApprovalVerifier,
    SignedTransitionApproval,
    TransitionApproval,
    authorize_transition_from_approval,
)
from agent_control_plane.transition_ledger import (
    SQLiteTransitionLedger,
    TransitionLedgerConflict,
    TransitionPhase,
)
from test_kubernetes_deployment_transition import (
    NOW,
    FakeDeploymentApi,
    build_transition,
)


ACTOR = Principal(
    type="controller",
    subject="agent-control-plane/controller-a",
)


def authority_chain(fixture, *, max_replicas=30):
    policy_input = TransitionPolicyInput.seal(
        policy_version="scale-policy/v1",
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=fixture["action"],
        principal=Principal(
            type="agent",
            subject="autoscaler-agent",
        ),
        context={
            "operation": "scale_deployment",
            "namespace": "prod",
            "replicas": 30,
        },
    )
    decision = evaluate_policy(
        policy_input=policy_input,
        evaluator=DeploymentScalePolicy(
            policy_version="scale-policy/v1",
            max_replicas=max_replicas,
            allowed_namespaces=("prod",),
        ),
    )
    approver = Principal(
        type="human",
        subject="sre@example.test",
    )
    approval = TransitionApproval.seal(
        approval_id="approval-001",
        approver=approver,
        decision=ApprovalDecision.APPROVE,
        transition=fixture["transition"],
        action=fixture["action"],
        policy_input=policy_input,
        policy_decision=decision,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    key = ApprovalSigningKey(
        key_id="test-approval-key",
        approver=approver,
        secret=b"transition-ledger-test-secret",
    )
    signed = SignedTransitionApproval.sign(
        approval,
        key=key,
    )
    verifier = HMACApprovalVerifier(
        keys={key.key_id: key},
    )
    authorization = None
    if max_replicas >= 30:
        authorization = authorize_transition_from_approval(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=policy_input,
            policy_decision=decision,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=1),
        )
    return (
        policy_input,
        decision,
        approval,
        signed,
        verifier,
        authorization,
    )


def lease_and_fence(fixture, authorization, *, epoch=11):
    holder = ACTOR
    lease = ExecutionLease(
        lease_id=f"lease-{epoch}",
        resource_uid=fixture["resource"].resource_uid,
        holder=holder,
        epoch=epoch,
        acquired_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    fence = ExecutionFence.bind(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=authorization,
        lease=lease,
    )
    return lease, fence


def test_full_authority_execution_verification_lifecycle_is_durable(tmp_path):
    api = FakeDeploymentApi()
    fixture = build_transition(api)
    (
        policy_input,
        decision,
        approval,
        signed_approval,
        approval_verifier,
        authorization,
    ) = authority_chain(fixture)
    lease, fence = lease_and_fence(
        fixture,
        authorization,
    )
    ledger = SQLiteTransitionLedger(tmp_path / "ledger.db")

    record = ledger.register(
        transition=fixture["transition"],
        action=fixture["action"],
        actor=ACTOR,
        occurred_at=NOW,
    )
    assert record.phase is TransitionPhase.PROPOSED
    assert record.state_version == 1

    record = ledger.record_policy(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        policy_input=policy_input,
        decision=decision,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert record.phase is TransitionPhase.POLICY_EVALUATED

    record = ledger.route_policy_result(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert record.phase is TransitionPhase.AWAITING_APPROVAL

    record = ledger.record_authorization(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        signed_approval=signed_approval,
        approval_verifier=approval_verifier,
        authorization=authorization,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=3),
    )
    assert record.phase is TransitionPhase.AUTHORIZED

    record = ledger.record_lease(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        lease=lease,
        fence=fence,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=4),
    )
    assert record.phase is TransitionPhase.LEASED

    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    attempt = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=authorization,
        fence=fence,
        prepared_at=NOW + timedelta(seconds=5),
    )
    record = ledger.record_execution_prepared(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        attempt=attempt,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=5),
    )
    assert record.phase is TransitionPhase.EXECUTION_PREPARED

    KubernetesDeploymentScaleProvider(api).execute(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=authorization,
        fence=fence,
        active_lease=lease,
        caller=ACTOR,
        now=NOW + timedelta(seconds=6),
        operation_id=attempt.operation_id,
    )

    record = ledger.start_reconcile(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=7),
    )
    assert record.phase is TransitionPhase.RECONCILING

    reconcile_deployment_attempt(
        api=api,
        journal=journal,
        attempt=attempt,
        transition=fixture["transition"],
        action=fixture["action"],
        namespace="prod",
        name="payment-api",
        reconciled_at=NOW + timedelta(seconds=8),
    )
    committed = journal.get(attempt.attempt_id)
    assert committed is not None

    record = ledger.record_execution_result(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        attempt=committed,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=8),
    )
    assert record.phase is TransitionPhase.EXECUTED

    record = ledger.start_verification(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=9),
    )
    api.deployment["status"]["readyReplicas"] = 30
    observation = KubernetesDeploymentObserver(api).observe(
        resource=fixture["resource"],
        principal=fixture["collector"],
        observed_at=NOW + timedelta(seconds=10),
    )
    verified = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=observation,
        safety_metrics={
            "error_rate": 0.001,
            "p95_ms": 200,
        },
        checked_at=NOW + timedelta(seconds=10),
    )
    assert verified.status is VerificationStatus.SUCCEEDED

    record = ledger.record_verification(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        result=verified,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=10),
    )
    assert record.phase is TransitionPhase.SUCCEEDED

    record = ledger.release(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=11),
    )
    assert record.phase is TransitionPhase.RELEASED
    assert record.state_version == 11

    restarted = SQLiteTransitionLedger(tmp_path / "ledger.db")
    verified_record = restarted.verify_history(record.transition_id)
    assert verified_record == record
    assert len(restarted.events(record.transition_id)) == 11


def test_policy_deny_is_terminal_and_cannot_skip_to_authorized(tmp_path):
    fixture = build_transition()
    policy_input, decision, _, _, _, _ = authority_chain(
        fixture,
        max_replicas=20,
    )
    ledger = SQLiteTransitionLedger(tmp_path / "ledger.db")
    record = ledger.register(
        transition=fixture["transition"],
        action=fixture["action"],
        actor=ACTOR,
        occurred_at=NOW,
    )
    record = ledger.record_policy(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        policy_input=policy_input,
        decision=decision,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=1),
    )
    record = ledger.route_policy_result(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=2),
    )

    assert record.phase is TransitionPhase.DENIED
    with pytest.raises(
        ProtocolViolation,
        match="illegal transition phase change",
    ):
        ledger.release(
            transition_id=record.transition_id,
            expected_version=record.state_version,
            actor=ACTOR,
            occurred_at=NOW + timedelta(seconds=3),
        )


def test_transition_phase_cannot_skip_authority_gates(tmp_path):
    fixture = build_transition()
    ledger = SQLiteTransitionLedger(tmp_path / "ledger.db")
    record = ledger.register(
        transition=fixture["transition"],
        action=fixture["action"],
        actor=ACTOR,
        occurred_at=NOW,
    )

    typed_bypasses = (
        lambda: ledger.record_lease(
            transition_id=record.transition_id,
            expected_version=record.state_version,
            lease=fixture["lease"],
            fence=fixture["fence"],
            actor=ACTOR,
            occurred_at=NOW + timedelta(seconds=1),
        ),
        lambda: ledger.start_reconcile(
            transition_id=record.transition_id,
            expected_version=record.state_version,
            actor=ACTOR,
            occurred_at=NOW + timedelta(seconds=1),
        ),
        lambda: ledger.start_verification(
            transition_id=record.transition_id,
            expected_version=record.state_version,
            actor=ACTOR,
            occurred_at=NOW + timedelta(seconds=1),
        ),
        lambda: ledger.release(
            transition_id=record.transition_id,
            expected_version=record.state_version,
            actor=ACTOR,
            occurred_at=NOW + timedelta(seconds=1),
        ),
    )
    for bypass in typed_bypasses:
        with pytest.raises(
            ProtocolViolation,
            match="illegal transition phase change",
        ):
            bypass()

def test_stale_controller_state_version_is_rejected_by_cas(tmp_path):
    fixture = build_transition()
    policy_input, decision, _, _, _, _ = authority_chain(fixture)
    ledger = SQLiteTransitionLedger(tmp_path / "ledger.db")
    record = ledger.register(
        transition=fixture["transition"],
        action=fixture["action"],
        actor=ACTOR,
        occurred_at=NOW,
    )
    stale_version = record.state_version

    ledger.record_policy(
        transition_id=record.transition_id,
        expected_version=stale_version,
        policy_input=policy_input,
        decision=decision,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(
        TransitionLedgerConflict,
        match="state_version changed",
    ):
        ledger.record_policy(
            transition_id=record.transition_id,
            expected_version=stale_version,
            policy_input=policy_input,
            decision=decision,
            actor=ACTOR,
            occurred_at=NOW + timedelta(seconds=2),
        )


def test_register_is_idempotent_after_lifecycle_has_advanced(tmp_path):
    fixture = build_transition()
    policy_input, decision, _, _, _, _ = authority_chain(fixture)
    ledger = SQLiteTransitionLedger(tmp_path / "ledger.db")
    first = ledger.register(
        transition=fixture["transition"],
        action=fixture["action"],
        actor=ACTOR,
        occurred_at=NOW,
    )
    advanced = ledger.record_policy(
        transition_id=first.transition_id,
        expected_version=first.state_version,
        policy_input=policy_input,
        decision=decision,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=1),
    )

    replayed = ledger.register(
        transition=fixture["transition"],
        action=fixture["action"],
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=2),
    )

    assert replayed == advanced
    assert len(ledger.events(first.transition_id)) == 2


def test_event_chain_tamper_is_detected(tmp_path):
    path = tmp_path / "ledger.db"
    fixture = build_transition()
    ledger = SQLiteTransitionLedger(path)
    record = ledger.register(
        transition=fixture["transition"],
        action=fixture["action"],
        actor=ACTOR,
        occurred_at=NOW,
    )

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE transition_events
            SET facts_json = ?
            WHERE transition_id = ? AND state_version = 1
            """,
            ('{"action_hash":"tampered"}', record.transition_id),
        )
        connection.commit()

    with pytest.raises(
        ProtocolViolation,
        match="event digest mismatch",
    ):
        ledger.verify_history(record.transition_id)


def test_materialized_state_tamper_is_detected_against_event_history(tmp_path):
    path = tmp_path / "ledger.db"
    fixture = build_transition()
    ledger = SQLiteTransitionLedger(path)
    record = ledger.register(
        transition=fixture["transition"],
        action=fixture["action"],
        actor=ACTOR,
        occurred_at=NOW,
    )

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE transition_ledger
            SET phase = ?
            WHERE transition_id = ?
            """,
            (
                TransitionPhase.SUCCEEDED.value,
                record.transition_id,
            ),
        )
        connection.commit()

    with pytest.raises(
        ProtocolViolation,
        match="materialized phase diverged",
    ):
        ledger.verify_history(record.transition_id)


def test_not_applied_attempt_can_retry_without_rewriting_history(tmp_path):
    fixture = build_transition()
    (
        policy_input,
        decision,
        approval,
        signed_approval,
        approval_verifier,
        authorization,
    ) = authority_chain(fixture)
    ledger = SQLiteTransitionLedger(tmp_path / "ledger.db")
    record = ledger.register(
        transition=fixture["transition"],
        action=fixture["action"],
        actor=ACTOR,
        occurred_at=NOW,
    )
    record = ledger.record_policy(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        policy_input=policy_input,
        decision=decision,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=1),
    )
    record = ledger.route_policy_result(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=2),
    )
    record = ledger.record_authorization(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        signed_approval=signed_approval,
        approval_verifier=approval_verifier,
        authorization=authorization,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=3),
    )

    lease1, fence1 = lease_and_fence(
        fixture,
        authorization,
        epoch=11,
    )
    record = ledger.record_lease(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        lease=lease1,
        fence=fence1,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=4),
    )

    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    attempt1 = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=authorization,
        fence=fence1,
        prepared_at=NOW + timedelta(seconds=5),
    )
    record = ledger.record_execution_prepared(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        attempt=attempt1,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=5),
    )
    journal.abort_not_applied(
        attempt1,
        completed_at=NOW + timedelta(seconds=6),
        result={"status": "NOT_APPLIED"},
    )
    aborted = journal.get(attempt1.attempt_id)
    record = ledger.record_execution_result(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        attempt=aborted,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=6),
    )
    assert record.phase is TransitionPhase.EXECUTION_NOT_APPLIED

    lease2, fence2 = lease_and_fence(
        fixture,
        authorization,
        epoch=12,
    )
    record = ledger.record_lease(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        lease=lease2,
        fence=fence2,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=7),
    )
    assert record.phase is TransitionPhase.LEASED

    attempt2 = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=authorization,
        fence=fence2,
        prepared_at=NOW + timedelta(seconds=8),
    )
    record = ledger.record_execution_prepared(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        attempt=attempt2,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=8),
    )

    assert attempt2.attempt_id != attempt1.attempt_id
    assert (
        record.facts[
            f"execution_attempt:{attempt1.attempt_id}:terminal_state"
        ]
        == "ABORTED"
    )
    assert (
        record.facts[
            f"execution_attempt:{attempt2.attempt_id}:lease_epoch"
        ]
        == 12
    )
    ledger.verify_history(record.transition_id)


def test_ledger_rejects_forged_signed_approval_before_authorized_state(tmp_path):
    fixture = build_transition()
    (
        policy_input,
        decision,
        approval,
        signed_approval,
        approval_verifier,
        authorization,
    ) = authority_chain(fixture)
    ledger = SQLiteTransitionLedger(tmp_path / "ledger.db")
    record = ledger.register(
        transition=fixture["transition"],
        action=fixture["action"],
        actor=ACTOR,
        occurred_at=NOW,
    )
    record = ledger.record_policy(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        policy_input=policy_input,
        decision=decision,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=1),
    )
    record = ledger.route_policy_result(
        transition_id=record.transition_id,
        expected_version=record.state_version,
        actor=ACTOR,
        occurred_at=NOW + timedelta(seconds=2),
    )
    forged = replace(
        signed_approval,
        signature="0" * 64,
    )

    with pytest.raises(
        ProtocolViolation,
        match="signature is invalid",
    ):
        ledger.record_authorization(
            transition_id=record.transition_id,
            expected_version=record.state_version,
            signed_approval=forged,
            approval_verifier=approval_verifier,
            authorization=authorization,
            actor=approval.approver,
            occurred_at=NOW + timedelta(seconds=3),
        )

    persisted = ledger.get(record.transition_id)
    assert persisted.phase is TransitionPhase.AWAITING_APPROVAL
    assert "authorization_hash" not in persisted.facts
