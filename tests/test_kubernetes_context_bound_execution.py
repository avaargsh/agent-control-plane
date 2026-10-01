from datetime import timedelta

import pytest

from agent_control_plane.context_transition import (
    TransitionProposalBinding,
    authorize_context_bound_transition,
    seal_context_bound_policy_input,
)
from agent_control_plane.kubernetes_deployment_transition import (
    ContextBoundExecutionContext,
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.policy_replay import (
    DeploymentScalePolicy,
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
from test_kubernetes_deployment_transition import NOW, build_transition


HUMAN = Principal(type="human", subject="ben")
PROPOSER = Principal(type="agent", subject="claude-code")
APPROVER = Principal(type="human", subject="sre-approver")
KEY = ApprovalSigningKey(
    key_id="kubernetes-context-approval-key-v1",
    approver=APPROVER,
    secret=b"kubernetes-context-approval-test-secret",
)


def _build_context_bound_execution(tmp_path):
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
        key=KEY,
    )
    verifier = HMACApprovalVerifier(keys={KEY.key_id: KEY})
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


def _execute(fixture, context, *, at=None):
    return KubernetesDeploymentScaleProvider(
        fixture["api"],
    ).execute_context_bound(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        caller=fixture["holder"],
        now=at or NOW + timedelta(seconds=5),
        context_binding=context,
    )


def test_context_bound_provider_patches_only_after_context_validation(
    tmp_path,
):
    fixture, _, _, context = _build_context_bound_execution(tmp_path)

    receipt = _execute(fixture, context)

    assert receipt.changed is True
    assert receipt.replayed is False
    assert fixture["api"].patch_calls == 1
    assert fixture["api"].deployment["spec"]["replicas"] == 30


def test_context_drift_after_authorization_blocks_provider_patch(tmp_path):
    fixture, store, proposal, context = _build_context_bound_execution(
        tmp_path
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
        _execute(
            fixture,
            context,
            at=NOW + timedelta(seconds=6),
        )

    assert fixture["api"].patch_calls == 0
    assert fixture["api"].deployment["spec"]["replicas"] == 20


def test_context_bound_replay_succeeds_while_context_is_fresh(tmp_path):
    fixture, _, _, context = _build_context_bound_execution(tmp_path)

    first = _execute(fixture, context)
    replay = _execute(
        fixture,
        context,
        at=NOW + timedelta(seconds=6),
    )

    assert first.changed is True
    assert replay.changed is False
    assert replay.replayed is True
    assert fixture["api"].patch_calls == 1


def test_context_drift_blocks_owned_provider_replay(tmp_path):
    fixture, store, proposal, context = _build_context_bound_execution(
        tmp_path
    )
    _execute(fixture, context)

    store.record_progress(
        work_id=proposal.work_id,
        expected_version=proposal.work_version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=6),
        state_patch={"post_execution_note": "world changed"},
    )

    with pytest.raises(
        ProtocolViolation,
        match="context proposal is stale",
    ):
        _execute(
            fixture,
            context,
            at=NOW + timedelta(seconds=7),
        )

    assert fixture["api"].patch_calls == 1
