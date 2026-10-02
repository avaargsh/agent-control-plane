from dataclasses import replace
from datetime import timedelta

import pytest

from agent_control_plane.context_transition import (
    TransitionProposalBindingV3,
    verify_policy_binds_proposal,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation
from context_testkit import (
    NOW,
    PROPOSER,
    build_context_bound_execution_v3,
    prepare_context_attempt_v3,
)
from kubernetes_testkit import build_transition


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
        now=at or NOW + timedelta(seconds=6),
        context_binding=context,
    )


def test_proposal_v3_binds_authority_generation_and_context(tmp_path):
    (
        _,
        store,
        overlay_store,
        authority,
        proposal,
        context,
    ) = build_context_bound_execution_v3(tmp_path)

    current_head = store.get_authority_head(proposal.work_id)
    overlay = overlay_store.get(work_id=proposal.work_id)

    assert current_head == authority
    assert proposal.proposal_version == "transition-proposal-binding/v3"
    assert proposal.authority_generation == authority.generation
    assert proposal.authority_state_hash == authority.authority_state_hash
    assert proposal.authority_hash == authority.authority_hash
    assert proposal.context_revision == overlay.context_revision
    assert proposal.context_overlay_hash == overlay.overlay_hash

    bound = context.policy_input.context["_agent_context_proposal"]
    assert bound["authority_generation"] == authority.generation
    assert bound["authority_state_hash"] == authority.authority_state_hash
    assert bound["authority_hash"] == authority.authority_hash
    assert bound["context_revision"] == proposal.context_revision

    verify_policy_binds_proposal(
        policy_input=context.policy_input,
        proposal=proposal,
    )


def test_proposal_v3_allows_context_drift_without_authority_drift(tmp_path):
    (
        fixture,
        store,
        overlay_store,
        authority,
        proposal,
        context,
        _,
        _,
        _,
    ) = prepare_context_attempt_v3(tmp_path)

    overlay_store.append(
        work_id=proposal.work_id,
        expected_revision=proposal.context_revision,
        actor=PROPOSER,
        entry_type="note",
        payload={"message": "context advanced after authorization"},
        created_at=NOW + timedelta(seconds=5),
    )

    assert store.get_authority_head(proposal.work_id) == authority

    receipt = _execute(
        fixture,
        context,
        at=NOW + timedelta(seconds=6),
    )

    assert receipt.changed is True
    assert fixture["api"].deployment["spec"]["replicas"] == 30


def test_proposal_v3_rejects_authority_generation_drift(tmp_path):
    (
        fixture,
        store,
        _,
        authority,
        proposal,
        context,
    ) = build_context_bound_execution_v3(tmp_path)

    current = store.get(proposal.work_id)
    updated = store.record_progress(
        work_id=proposal.work_id,
        expected_version=current.version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=5),
        state_patch={"target_replicas": 40},
    )
    advanced = store.get_authority_head(proposal.work_id)

    assert updated.version == current.version + 1
    assert advanced.generation == authority.generation + 1
    assert advanced.authority_hash != authority.authority_hash

    with pytest.raises(
        ProtocolViolation,
        match="authority generation is stale",
    ):
        _execute(
            fixture,
            context,
            at=NOW + timedelta(seconds=6),
        )

    assert fixture["api"].patch_calls == 0


def test_proposal_v3_rejects_stale_authority_head_at_seal(tmp_path):
    fixture = build_transition()
    (
        _,
        store,
        overlay_store,
        old_head,
        proposal,
        _,
    ) = build_context_bound_execution_v3(tmp_path)

    current = store.get(proposal.work_id)
    store.record_progress(
        work_id=proposal.work_id,
        expected_version=current.version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=5),
        state_patch={"phase": "new-authority"},
    )
    projection = store.project(
        work_id=proposal.work_id,
        consumer=PROPOSER,
    )
    overlay = overlay_store.get(
        work_id=proposal.work_id,
    )

    with pytest.raises(
        ProtocolViolation,
        match="current work authority state diverged",
    ):
        TransitionProposalBindingV3.seal(
            proposal_id="proposal-v3-stale-authority",
            proposer=PROPOSER,
            transition=fixture["transition"],
            projection=projection,
            authority_head=old_head,
            context_overlay=overlay,
            created_at=NOW + timedelta(seconds=6),
        )


def test_proposal_v3_detects_authority_binding_tampering(tmp_path):
    (
        _,
        _,
        _,
        _,
        proposal,
        _,
    ) = build_context_bound_execution_v3(tmp_path)

    tampered = replace(
        proposal,
        authority_generation=proposal.authority_generation + 1,
    )

    with pytest.raises(
        ProtocolViolation,
        match="proposal v3 binding digest mismatch",
    ):
        tampered.verify()
