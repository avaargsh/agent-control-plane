from dataclasses import replace
from datetime import timedelta

import pytest

from agent_control_plane.context_transition import (
    TransitionProposalBindingV2,
    verify_policy_binds_proposal,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation
from context_testkit import (
    NOW,
    PROPOSER,
    build_context_bound_execution_v2,
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


def test_proposal_v2_binds_authority_and_observed_overlay(tmp_path):
    (
        _,
        _,
        overlay_store,
        proposal,
        context,
    ) = build_context_bound_execution_v2(tmp_path)
    overlay = overlay_store.get(work_id=proposal.work_id)

    assert proposal.proposal_version == "transition-proposal-binding/v2"
    assert proposal.context_revision == overlay.context_revision
    assert proposal.context_head_hash == overlay.head_hash
    assert proposal.context_overlay_hash == overlay.overlay_hash

    bound = context.policy_input.context["_agent_context_proposal"]
    assert bound["proposal_version"] == proposal.proposal_version
    assert bound["context_revision"] == proposal.context_revision
    assert bound["context_head_hash"] == proposal.context_head_hash
    assert bound["context_overlay_hash"] == proposal.context_overlay_hash

    verify_policy_binds_proposal(
        policy_input=context.policy_input,
        proposal=proposal,
    )


def test_overlay_drift_after_authorization_does_not_block_execution(
    tmp_path,
):
    (
        fixture,
        store,
        overlay_store,
        proposal,
        context,
    ) = build_context_bound_execution_v2(tmp_path)
    authority_before = store.get(proposal.work_id)

    overlay_store.append(
        work_id=proposal.work_id,
        expected_revision=proposal.context_revision,
        actor=PROPOSER,
        entry_type="note",
        payload={"message": "new context after approval"},
        created_at=NOW + timedelta(seconds=5),
    )

    authority_after = store.get(proposal.work_id)
    overlay_after = overlay_store.get(work_id=proposal.work_id)

    assert authority_after.version == authority_before.version
    assert authority_after.snapshot_hash == authority_before.snapshot_hash
    assert overlay_after.context_revision == proposal.context_revision + 1

    receipt = _execute(
        fixture,
        context,
        at=NOW + timedelta(seconds=6),
    )

    assert receipt.changed is True
    assert fixture["api"].deployment["spec"]["replicas"] == 30


def test_authority_drift_still_blocks_proposal_v2_execution(tmp_path):
    (
        fixture,
        store,
        _,
        proposal,
        context,
    ) = build_context_bound_execution_v2(tmp_path)

    store.record_progress(
        work_id=proposal.work_id,
        expected_version=proposal.work_version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=5),
        state_patch={"target_replicas": 40},
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


def test_proposal_v2_rejects_overlay_observed_against_old_authority(
    tmp_path,
):
    fixture = build_transition()
    (
        _,
        store,
        overlay_store,
        proposal,
        _,
    ) = build_context_bound_execution_v2(tmp_path)
    stale_overlay = overlay_store.get(work_id=proposal.work_id)

    progressed = store.record_progress(
        work_id=proposal.work_id,
        expected_version=proposal.work_version,
        actor=PROPOSER,
        updated_at=NOW + timedelta(seconds=5),
        state_patch={"phase": "new-authority"},
    )
    projection = store.project(
        work_id=proposal.work_id,
        consumer=PROPOSER,
    )
    assert projection.work.version == progressed.version

    with pytest.raises(
        ProtocolViolation,
        match="overlay authority version does not match projection",
    ):
        TransitionProposalBindingV2.seal(
            proposal_id="proposal-v2-stale-overlay",
            proposer=PROPOSER,
            transition=fixture["transition"],
            projection=projection,
            context_overlay=stale_overlay,
            created_at=NOW + timedelta(seconds=6),
        )


def test_proposal_v2_detects_overlay_binding_tampering(tmp_path):
    (
        _,
        _,
        _,
        proposal,
        _,
    ) = build_context_bound_execution_v2(tmp_path)
    tampered = replace(
        proposal,
        context_head_hash="sha256:" + "f" * 64,
    )

    with pytest.raises(
        ProtocolViolation,
        match="proposal v2 binding digest mismatch",
    ):
        tampered.verify()
