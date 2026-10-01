from __future__ import annotations

from datetime import timedelta

from agent_control_plane.context_transition import (
    TransitionProposalBinding,
    authorize_context_bound_transition,
    execution_context_provenance,
    seal_context_bound_policy_input,
)
from agent_control_plane.execution_journal import SQLiteExecutionJournal
from agent_control_plane.kubernetes_deployment_transition import (
    ContextBoundExecutionContext,
)
from agent_control_plane.policy_replay import (
    DeploymentScalePolicy,
    evaluate_policy,
)
from agent_control_plane.state_transition_protocol import (
    ExecutionFence,
    Principal,
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
APPROVAL_KEY = ApprovalSigningKey(
    key_id="kubernetes-context-approval-key-v1",
    approver=APPROVER,
    secret=b"kubernetes-context-approval-test-secret",
)


def build_context_bound_execution(tmp_path):
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
        proposal_id="proposal-k8s-scale-001",
        proposer=PROPOSER,
        transition=fixture["transition"],
        projection=projection,
        created_at=NOW + timedelta(seconds=2),
    )
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
        approval_id="approval-k8s-context-scale-001",
        approver=APPROVER,
        decision=ApprovalDecision.APPROVE,
        transition=fixture["transition"],
        action=fixture["action"],
        policy_input=policy_input,
        policy_decision=decision,
        issued_at=NOW + timedelta(seconds=3),
        expires_at=NOW + timedelta(minutes=5),
        reason="approve context-bound Kubernetes scale",
    )
    signed = SignedTransitionApproval.sign(
        approval,
        key=APPROVAL_KEY,
    )
    verifier = HMACApprovalVerifier(
        keys={APPROVAL_KEY.key_id: APPROVAL_KEY},
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
    fixture["authorization"] = authorization
    fixture["fence"] = ExecutionFence.bind(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=authorization,
        lease=fixture["lease"],
    )
    context = ContextBoundExecutionContext(
        policy_input=policy_input,
        proposal=proposal,
        store=store,
        signed_approval=signed,
        approval_verifier=verifier,
    )
    return fixture, store, proposal, context


def prepare_context_attempt(tmp_path):
    fixture, store, proposal, context = build_context_bound_execution(
        tmp_path
    )
    journal = SQLiteExecutionJournal(tmp_path / "execution.db")
    provenance = execution_context_provenance(proposal)
    attempt = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW + timedelta(seconds=4),
        context_provenance=provenance,
    )
    return (
        fixture,
        store,
        proposal,
        context,
        journal,
        provenance,
        attempt,
    )
