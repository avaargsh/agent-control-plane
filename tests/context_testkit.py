from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from agent_control_plane.context_overlay import SQLiteContextOverlayStore
from agent_control_plane.context_transition import (
    ContextProposalBinding,
    TransitionProposalBinding,
    TransitionProposalBindingV2,
    TransitionProposalBindingV3,
    authorize_context_bound_transition,
    build_execution_context_provenance,
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


def _create_claimed_work(
    tmp_path: Path,
    *,
    db_name: str,
    work_id: str,
) -> tuple[
    dict[str, Any],
    SQLiteWorkContextStore,
    SQLiteContextOverlayStore,
]:
    fixture = build_transition()
    path = tmp_path / db_name
    store = SQLiteWorkContextStore(path)
    overlay_store = SQLiteContextOverlayStore(path)

    created = store.create(
        work_id=work_id,
        namespace="repo/payment-api",
        goal="Scale payment-api from 20 to 30 replicas",
        actor=HUMAN,
        created_at=NOW,
        state={
            "phase": "proposal",
            "target_replicas": 30,
        },
    )
    store.claim(
        work_id=created.work_id,
        expected_version=created.version,
        agent=PROPOSER,
        claimed_at=NOW + timedelta(seconds=1),
    )
    return fixture, store, overlay_store


def _authorize(
    *,
    fixture: dict[str, Any],
    store: SQLiteWorkContextStore,
    proposal: ContextProposalBinding,
    approval_id: str,
    reason: str,
) -> ContextBoundExecutionContext:
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
        approval_id=approval_id,
        approver=APPROVER,
        decision=ApprovalDecision.APPROVE,
        transition=fixture["transition"],
        action=fixture["action"],
        policy_input=policy_input,
        policy_decision=decision,
        issued_at=NOW + timedelta(seconds=3),
        expires_at=NOW + timedelta(minutes=5),
        reason=reason,
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
    return ContextBoundExecutionContext(
        policy_input=policy_input,
        proposal=proposal,
        store=store,
        signed_approval=signed,
        approval_verifier=verifier,
    )


def _append_initial_overlay(
    *,
    overlay_store: SQLiteContextOverlayStore,
    work_id: str,
) -> None:
    overlay_store.append(
        work_id=work_id,
        expected_revision=0,
        actor=PROPOSER,
        entry_type="observation",
        payload={"note": "proposal context"},
        created_at=NOW + timedelta(milliseconds=1500),
    )


def build_context_bound_execution(tmp_path):
    fixture, store, _ = _create_claimed_work(
        tmp_path,
        db_name="context.db",
        work_id="scale-payment-api",
    )
    projection = store.project(
        work_id="scale-payment-api",
        consumer=PROPOSER,
    )
    proposal = TransitionProposalBinding.seal(
        proposal_id="proposal-k8s-scale-001",
        proposer=PROPOSER,
        transition=fixture["transition"],
        projection=projection,
        created_at=NOW + timedelta(seconds=2),
    )
    context = _authorize(
        fixture=fixture,
        store=store,
        proposal=proposal,
        approval_id="approval-k8s-context-scale-001",
        reason="approve context-bound Kubernetes scale",
    )
    return fixture, store, proposal, context


def build_context_bound_execution_v2(tmp_path):
    fixture, store, overlay_store = _create_claimed_work(
        tmp_path,
        db_name="context-v2.db",
        work_id="scale-payment-api-v2",
    )
    _append_initial_overlay(
        overlay_store=overlay_store,
        work_id="scale-payment-api-v2",
    )
    projection = store.project(
        work_id="scale-payment-api-v2",
        consumer=PROPOSER,
    )
    overlay = overlay_store.get(
        work_id="scale-payment-api-v2",
    )
    proposal = TransitionProposalBindingV2.seal(
        proposal_id="proposal-k8s-scale-v2-001",
        proposer=PROPOSER,
        transition=fixture["transition"],
        projection=projection,
        context_overlay=overlay,
        created_at=NOW + timedelta(seconds=2),
    )
    context = _authorize(
        fixture=fixture,
        store=store,
        proposal=proposal,
        approval_id="approval-k8s-context-scale-v2-001",
        reason="approve context-bound Kubernetes scale v2",
    )
    return fixture, store, overlay_store, proposal, context


def build_context_bound_execution_v3(tmp_path):
    fixture, store, overlay_store = _create_claimed_work(
        tmp_path,
        db_name="context-v3.db",
        work_id="scale-payment-api-v3",
    )
    _append_initial_overlay(
        overlay_store=overlay_store,
        work_id="scale-payment-api-v3",
    )
    projection = store.project(
        work_id="scale-payment-api-v3",
        consumer=PROPOSER,
    )
    overlay = overlay_store.get(
        work_id="scale-payment-api-v3",
    )
    authority = store.get_authority_head(
        "scale-payment-api-v3"
    )
    proposal = TransitionProposalBindingV3.seal(
        proposal_id="proposal-k8s-scale-v3-001",
        proposer=PROPOSER,
        transition=fixture["transition"],
        projection=projection,
        authority_head=authority,
        context_overlay=overlay,
        created_at=NOW + timedelta(seconds=2),
    )
    context = _authorize(
        fixture=fixture,
        store=store,
        proposal=proposal,
        approval_id="approval-k8s-context-scale-v3-001",
        reason="approve authority-bound Kubernetes scale v3",
    )
    return (
        fixture,
        store,
        overlay_store,
        authority,
        proposal,
        context,
    )


def _prepare_attempt(
    *,
    tmp_path: Path,
    fixture: dict[str, Any],
    proposal: ContextProposalBinding,
    journal_name: str,
):
    journal = SQLiteExecutionJournal(tmp_path / journal_name)
    provenance = build_execution_context_provenance(proposal)
    attempt = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW + timedelta(seconds=4),
        context_provenance=provenance,
    )
    return journal, provenance, attempt


def prepare_context_attempt(tmp_path):
    fixture, store, proposal, context = build_context_bound_execution(
        tmp_path
    )
    journal, provenance, attempt = _prepare_attempt(
        tmp_path=tmp_path,
        fixture=fixture,
        proposal=proposal,
        journal_name="execution.db",
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


def prepare_context_attempt_v2(tmp_path):
    (
        fixture,
        store,
        overlay_store,
        proposal,
        context,
    ) = build_context_bound_execution_v2(tmp_path)
    journal, provenance, attempt = _prepare_attempt(
        tmp_path=tmp_path,
        fixture=fixture,
        proposal=proposal,
        journal_name="execution-v2.db",
    )
    return (
        fixture,
        store,
        overlay_store,
        proposal,
        context,
        journal,
        provenance,
        attempt,
    )


def prepare_context_attempt_v3(tmp_path):
    (
        fixture,
        store,
        overlay_store,
        authority,
        proposal,
        context,
    ) = build_context_bound_execution_v3(tmp_path)
    journal, provenance, attempt = _prepare_attempt(
        tmp_path=tmp_path,
        fixture=fixture,
        proposal=proposal,
        journal_name="execution-v3.db",
    )
    return (
        fixture,
        store,
        overlay_store,
        authority,
        proposal,
        context,
        journal,
        provenance,
        attempt,
    )
