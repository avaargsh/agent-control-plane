from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest

from agent_control_plane.provable_execution import (
    ObservationSnapshot,
    PolicyDecision,
    PolicyDecisionEnvelope,
    ReconciliationResult,
    ReconciliationStatus,
    TransitionPlan,
    VerificationCondition,
    VerificationReport,
    VerificationStatus,
)
from agent_control_plane.state_transition_protocol import (
    Principal,
    ProtocolViolation,
    ResourceIdentity,
    canonical_digest,
)


NOW = datetime(2026, 10, 2, 4, 30, tzinfo=timezone.utc)
OBSERVER = Principal(type="controller", subject="observer")
VERIFIER = Principal(type="controller", subject="verifier")


def kubernetes_plan():
    subject = ResourceIdentity(
        provider="kubernetes",
        resource_uid="uid-payment-api",
        namespace="prod",
        kind="Deployment",
        name="payment-api",
    )
    observation = ObservationSnapshot.capture(
        subject=subject,
        observed_version="resourceVersion:991",
        observed_at=NOW,
        state={
            "replicas": 20,
            "readyReplicas": 20,
        },
        observer=OBSERVER,
    )
    plan = TransitionPlan.seal(
        plan_id="plan-k8s-scale",
        observation=observation,
        before={"replicas": 20},
        desired={"replicas": 30},
        provider="kubernetes",
        operation="scale_deployment",
        parameters={"replicas": 30},
        preconditions={
            "version": observation.observed_version,
            "generation": 7,
        },
        created_at=NOW + timedelta(seconds=1),
    )
    return observation, plan


def github_plan():
    subject = ResourceIdentity(
        provider="github",
        resource_uid="github:acme/payments#123",
        namespace="acme/payments",
        kind="PullRequest",
        name="123",
    )
    observation = ObservationSnapshot.capture(
        subject=subject,
        observed_version="git-sha:abc123",
        observed_at=NOW,
        state={
            "state": "open",
            "head_sha": "abc123",
            "mergeable": True,
        },
        observer=OBSERVER,
    )
    plan = TransitionPlan.seal(
        plan_id="plan-github-merge",
        observation=observation,
        before={
            "state": "open",
            "head_sha": "abc123",
        },
        desired={
            "state": "merged",
            "head_sha": "abc123",
        },
        provider="github",
        operation="merge_pull_request",
        parameters={"method": "squash"},
        preconditions={
            "version": observation.observed_version,
            "approved_head_sha": "abc123",
        },
        created_at=NOW + timedelta(seconds=1),
    )
    return observation, plan


def test_kubernetes_and_github_use_identical_core_schema():
    _, kubernetes = kubernetes_plan()
    _, github = github_plan()

    assert [field.name for field in fields(kubernetes)] == [
        field.name for field in fields(github)
    ]
    assert kubernetes.provider == "kubernetes"
    assert github.provider == "github"
    assert kubernetes.plan_version == github.plan_version
    assert kubernetes.plan_hash != github.plan_hash

    core_fields = {field.name for field in fields(TransitionPlan)}
    assert "resource_version" not in core_fields
    assert "generation" not in core_fields
    assert "head_sha" not in core_fields
    assert "mergeable" not in core_fields


def test_observed_version_is_an_opaque_provider_token():
    k8s_observation, _ = kubernetes_plan()
    github_observation, _ = github_plan()

    assert k8s_observation.observed_version == "resourceVersion:991"
    assert github_observation.observed_version == "git-sha:abc123"
    k8s_observation.verify()
    github_observation.verify()


def test_plan_binds_exact_observation_and_parameters():
    observation, plan = github_plan()
    plan.verify(observation)

    object.__setattr__(
        plan,
        "parameters",
        {"method": "merge"},
    )

    with pytest.raises(
        ProtocolViolation,
        match="transition plan digest mismatch",
    ):
        plan.verify(observation)


def test_plan_rejects_different_observation_even_for_same_subject():
    observation, plan = kubernetes_plan()
    newer = ObservationSnapshot.capture(
        subject=observation.subject,
        observed_version="resourceVersion:992",
        observed_at=NOW + timedelta(seconds=10),
        state={
            "replicas": 25,
            "readyReplicas": 25,
        },
        observer=OBSERVER,
    )

    with pytest.raises(
        ProtocolViolation,
        match="transition plan observation binding mismatch",
    ):
        plan.verify(newer)


def test_external_policy_decision_is_digest_bound_not_recomputed():
    _, plan = github_plan()
    envelope = PolicyDecisionEnvelope.seal(
        engine_identity="external-policy-engine",
        policy_set_digest="sha256:" + "1" * 64,
        policy_input_digest=canonical_digest(
            {
                "plan_hash": plan.plan_hash,
                "principal": "release-bot",
            }
        ),
        decision=PolicyDecision.ALLOW,
        determining_policy_refs=(
            "repo-merge-policy",
            "change-window-policy",
        ),
        evaluated_at=NOW + timedelta(seconds=2),
    )

    envelope.verify()
    assert envelope.decision is PolicyDecision.ALLOW
    assert envelope.determining_policy_refs == (
        "change-window-policy",
        "repo-merge-policy",
    )


def test_verification_report_preserves_unknown_as_first_class_state():
    before, plan = kubernetes_plan()
    after = ObservationSnapshot.capture(
        subject=before.subject,
        observed_version="resourceVersion:1001",
        observed_at=NOW + timedelta(seconds=30),
        state={
            "replicas": 30,
            "readyReplicas": 27,
        },
        observer=OBSERVER,
    )
    report = VerificationReport.seal(
        plan=plan,
        after_observation=after,
        conditions=(
            VerificationCondition(
                condition_type="DesiredStateReached",
                status=VerificationStatus.TRUE,
                reason="SpecConverged",
                message="desired replicas is 30",
                evidence_digest=after.observation_hash,
            ),
            VerificationCondition(
                condition_type="HealthSatisfied",
                status=VerificationStatus.UNKNOWN,
                reason="StillStabilizing",
                message="only 27 replicas are ready",
                evidence_digest=after.observation_hash,
            ),
        ),
        verifier=VERIFIER,
        verified_at=NOW + timedelta(seconds=31),
    )

    report.verify(plan=plan, after_observation=after)
    assert report.has_unknown
    assert not report.succeeded


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ReconciliationStatus.APPLIED, "APPLIED"),
        (ReconciliationStatus.NOT_APPLIED, "NOT_APPLIED"),
        (ReconciliationStatus.UNKNOWN, "UNKNOWN"),
    ],
)
def test_reconciliation_status_is_provider_neutral(status, expected):
    after, _ = github_plan()
    result = ReconciliationResult.seal(
        status=status,
        attempt_id="attempt-1",
        operation_id="operation-1",
        observed_after=after,
        reason="fixture",
        details={"proof": "provider observation"},
        reconciled_at=NOW + timedelta(seconds=20),
    )

    result.verify()
    assert result.status.value == expected
