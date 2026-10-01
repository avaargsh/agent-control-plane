from datetime import timedelta

import pytest

from agent_control_plane.context_transition import (
    TransitionProposalBinding,
    authorize_context_bound_transition,
    seal_context_bound_policy_input,
    validate_context_bound_execution,
    verify_policy_binds_proposal,
)
from agent_control_plane.policy_replay import (
    DeploymentScalePolicy,
    TransitionPolicyInput,
    evaluate_policy,
)
from agent_control_plane.state_transition_protocol import (
    ExecutionFence,
    Principal,
    ProtocolViolation,
)
from agent_control_plane.transition_approval import (
    ApprovalDecision,
    ApprovalSigningKey,
    HMACApprovalVerifier,
    SignedTransitionApproval,
    TransitionApproval,
)
from agent_control_plane.work_context import SQLiteWorkContextStore
from kubernetes_testkit import NOW, build_transition


HUMAN = Principal(type="human", subject="ben")
PROPOSER = Principal(type="agent", subject="claude-code")
APPROVER = Principal(type="human", subject="sre-approver")
KEY = ApprovalSigningKey(
    key_id="context-approval-key-v1",
    approver=APPROVER,
    secret=b"context-approval-test-secret",
)


def _context_fixture(tmp_path):
    fixture = build_transition()
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = store.create(
        work_id="scale-payment-api",
        namespace="repo/payment-api",
        goal="Scale payment-api from 20 to 30 replicas",
        actor=HUMAN,
        created_at=NOW,
        state={
            "phase": "proposal",
            "target_replicas": 30,
        },
    )
    claimed = store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=PROPOSER,
        claimed_at=NOW + timedelta(seconds=1),
    )
    projection = store.project(
        work_id=created.work_id,
        consumer=PROPOSER,
    )
    assert projection.work.version == claimed.version

    proposal = TransitionProposalBinding.seal(
        proposal_id="proposal-scale-payment-api-001",
        proposer=PROPOSER,
        transition=fixture["transition"],
        projection=projection,
        created_at=NOW + timedelta(seconds=2),
    )
    return fixture, store, proposal


def _policy_chain(fixture, store, proposal):
    policy_input = seal_context_bound_policy_input(
        policy_version="scale-policy/v1",
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=fixture["action"],
        principal=PROPOSER,
        context={
            "operation": "scale_deployment",
            "namespace": "prod",
            "replicas": 30,
        },
        proposal=proposal,
        store=store,
    )
    decision = evaluate_policy(
        policy_input=policy_input,
        evaluator=DeploymentScalePolicy(
            policy_version="scale-policy/v1",
            max_replicas=30,
            allowed_namespaces=("prod",),
        ),
    )
    approval = TransitionApproval.seal(
        approval_id="approval-context-scale-001",
        approver=APPROVER,
        decision=ApprovalDecision.APPROVE,
        transition=fixture["transition"],
        action=fixture["action"],
        policy_input=policy_input,
        policy_decision=decision,
        issued_at=NOW + timedelta(seconds=3),
        expires_at=NOW + timedelta(minutes=5),
        reason="approve context-bound deployment scale",
    )
    signed = SignedTransitionApproval.sign(
        approval,
        key=KEY,
    )
    verifier = HMACApprovalVerifier(keys={KEY.key_id: KEY})
    return policy_input, decision, approval, signed, verifier


def test_policy_input_digest_binds_projection_and_work_snapshot(tmp_path):
    fixture, store, proposal = _context_fixture(tmp_path)
    policy_input, _, approval, _, _ = _policy_chain(
        fixture,
        store,
        proposal,
    )

    verify_policy_binds_proposal(
        policy_input=policy_input,
        proposal=proposal,
    )

    binding = policy_input.context["_agent_context_proposal"]
    assert binding["proposal_hash"] == proposal.proposal_hash
    assert binding["projection_hash"] == proposal.projection_hash
    assert binding["work_version"] == proposal.work_version
    assert (
        binding["work_snapshot_hash"]
        == proposal.work_snapshot_hash
    )
    assert approval.policy_input_hash == policy_input.input_hash


def test_context_bound_authorization_succeeds_while_projection_is_fresh(
    tmp_path,
):
    fixture, store, proposal = _context_fixture(tmp_path)
    policy_input, decision, approval, signed, verifier = _policy_chain(
        fixture,
        store,
        proposal,
    )

    authorization = authorize_context_bound_transition(
        transition=fixture["transition"],
        action=fixture["action"],
        policy_input=policy_input,
        policy_decision=decision,
        proposal=proposal,
        store=store,
        signed_approval=signed,
        approval_verifier=verifier,
        authorization_expires_at=NOW + timedelta(minutes=4),
        now=NOW + timedelta(seconds=4),
    )

    assert authorization.approval_hash == approval.approval_hash
    assert authorization.principal == PROPOSER


def test_authorization_rejects_work_that_changed_after_proposal(tmp_path):
    fixture, store, proposal = _context_fixture(tmp_path)
    policy_input, decision, _, signed, verifier = _policy_chain(
        fixture,
        store,
        proposal,
    )

    store.record_progress(
        work_id=proposal.work_id,
        expected_version=proposal.work_version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=4),
        state_patch={"new_fact": "work moved after proposal"},
    )

    with pytest.raises(
        ProtocolViolation,
        match="context proposal is stale",
    ):
        authorize_context_bound_transition(
            transition=fixture["transition"],
            action=fixture["action"],
            policy_input=policy_input,
            policy_decision=decision,
            proposal=proposal,
            store=store,
            signed_approval=signed,
            approval_verifier=verifier,
            authorization_expires_at=NOW + timedelta(minutes=4),
            now=NOW + timedelta(seconds=5),
        )


def test_execution_rechecks_signed_proposal_chain_and_freshness(tmp_path):
    fixture, store, proposal = _context_fixture(tmp_path)
    policy_input, decision, _, signed, verifier = _policy_chain(
        fixture,
        store,
        proposal,
    )
    authorization = authorize_context_bound_transition(
        transition=fixture["transition"],
        action=fixture["action"],
        policy_input=policy_input,
        policy_decision=decision,
        proposal=proposal,
        store=store,
        signed_approval=signed,
        approval_verifier=verifier,
        authorization_expires_at=NOW + timedelta(minutes=4),
        now=NOW + timedelta(seconds=4),
    )
    fence = ExecutionFence.bind(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=authorization,
        lease=fixture["lease"],
    )

    validate_context_bound_execution(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=authorization,
        fence=fence,
        active_lease=fixture["lease"],
        current_generation=7,
        caller=fixture["holder"],
        now=NOW + timedelta(seconds=5),
        policy_input=policy_input,
        proposal=proposal,
        store=store,
        signed_approval=signed,
        approval_verifier=verifier,
    )


def test_execution_rejects_context_drift_after_authorization(tmp_path):
    fixture, store, proposal = _context_fixture(tmp_path)
    policy_input, decision, _, signed, verifier = _policy_chain(
        fixture,
        store,
        proposal,
    )
    authorization = authorize_context_bound_transition(
        transition=fixture["transition"],
        action=fixture["action"],
        policy_input=policy_input,
        policy_decision=decision,
        proposal=proposal,
        store=store,
        signed_approval=signed,
        approval_verifier=verifier,
        authorization_expires_at=NOW + timedelta(minutes=4),
        now=NOW + timedelta(seconds=4),
    )
    fence = ExecutionFence.bind(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=authorization,
        lease=fixture["lease"],
    )

    store.record_progress(
        work_id=proposal.work_id,
        expected_version=proposal.work_version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=5),
        state_patch={"phase": "changed-after-authorization"},
    )

    with pytest.raises(
        ProtocolViolation,
        match="context proposal is stale",
    ):
        validate_context_bound_execution(
            transition=fixture["transition"],
            evidence=fixture["evidence"],
            outcome_contract=fixture["outcome"],
            action=fixture["action"],
            authorization=authorization,
            fence=fence,
            active_lease=fixture["lease"],
            current_generation=7,
            caller=fixture["holder"],
            now=NOW + timedelta(seconds=6),
            policy_input=policy_input,
            proposal=proposal,
            store=store,
            signed_approval=signed,
            approval_verifier=verifier,
        )


def test_plain_policy_input_cannot_enter_context_bound_authorization(
    tmp_path,
):
    fixture, store, proposal = _context_fixture(tmp_path)
    plain = TransitionPolicyInput.seal(
        policy_version="scale-policy/v1",
        evidence=fixture["evidence"],
        transition=fixture["transition"],
        action=fixture["action"],
        principal=PROPOSER,
        context={
            "operation": "scale_deployment",
            "namespace": "prod",
            "replicas": 30,
        },
    )

    with pytest.raises(
        ProtocolViolation,
        match="missing transition proposal context binding",
    ):
        verify_policy_binds_proposal(
            policy_input=plain,
            proposal=proposal,
        )


def test_proposal_requires_current_work_owner_as_consumer(tmp_path):
    fixture = build_transition()
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    created = store.create(
        work_id="scale-payment-api",
        namespace="repo/payment-api",
        goal="Scale payment-api",
        actor=HUMAN,
        created_at=NOW,
    )
    store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=PROPOSER,
        claimed_at=NOW + timedelta(seconds=1),
    )
    wrong_consumer = Principal(type="agent", subject="codex")
    projection = store.project(
        work_id=created.work_id,
        consumer=wrong_consumer,
    )

    with pytest.raises(
        ProtocolViolation,
        match="consumer does not match proposer",
    ):
        TransitionProposalBinding.seal(
            proposal_id="bad-proposal",
            proposer=PROPOSER,
            transition=fixture["transition"],
            projection=projection,
            created_at=NOW + timedelta(seconds=2),
        )
