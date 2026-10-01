from datetime import timedelta

import pytest

from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation
from context_testkit import (
    NOW,
    PROPOSER,
    build_context_bound_execution,
)


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
    fixture, _, _, context = build_context_bound_execution(tmp_path)

    receipt = _execute(fixture, context)

    assert receipt.changed is True
    assert receipt.replayed is False
    assert fixture["api"].patch_calls == 1
    assert fixture["api"].deployment["spec"]["replicas"] == 30


def test_context_drift_after_authorization_blocks_provider_patch(tmp_path):
    fixture, store, proposal, context = build_context_bound_execution(
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
    fixture, _, _, context = build_context_bound_execution(tmp_path)

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
    fixture, store, proposal, context = build_context_bound_execution(
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
