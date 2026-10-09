import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_control_plane.github_pull_request_transition import (
    GitHubPullRequestMergeProvider,
)
from agent_control_plane.provable_execution import (
    ReconciliationStatus,
)
from agent_control_plane.runtime_clients import (
    RuntimeMutationOwnershipUncertain,
    RuntimeMutationUncertain,
)
from agent_control_plane.state_transition_protocol import (
    Principal,
    ProtocolViolation,
    ResourceIdentity,
)


NOW = datetime(2026, 10, 2, 5, 30, tzinfo=timezone.utc)
OBSERVER = Principal(type="controller", subject="github-observer")
ACTOR = Principal(type="controller", subject="github-executor")
VERIFIER = Principal(type="controller", subject="github-verifier")


class FakeGitHubPullRequestApi:
    def __init__(self) -> None:
        self.merge_calls = 0
        self.last_merge = None
        self.pull_request = {
            "number": 123,
            "state": "open",
            "merged": False,
            "head": {"sha": "abc123"},
            "merge_commit_sha": None,
        }
        self.commits = {}

    @staticmethod
    def _copy(value):
        return json.loads(json.dumps(value))

    def get_pull_request(self, *, owner, repo, number):
        if owner != "acme" or repo != "payments" or number != 123:
            return None
        return self._copy(self.pull_request)

    def merge_pull_request(
        self,
        *,
        owner,
        repo,
        number,
        head_sha,
        merge_method,
        commit_message,
    ):
        self.merge_calls += 1
        self.last_merge = {
            "owner": owner,
            "repo": repo,
            "number": number,
            "head_sha": head_sha,
            "merge_method": merge_method,
            "commit_message": commit_message,
        }
        if self.pull_request["head"]["sha"] != head_sha:
            raise RuntimeError("head SHA conflict")
        if self.pull_request["merged"]:
            return {
                "merged": False,
                "message": "Pull Request is not mergeable",
                "sha": self.pull_request["merge_commit_sha"],
            }
        merge_sha = "merge-abc123"
        self.pull_request["state"] = "closed"
        self.pull_request["merged"] = True
        self.pull_request["merge_commit_sha"] = merge_sha
        self.commits[merge_sha] = {
            "sha": merge_sha,
            "commit": {"message": commit_message},
        }
        return {
            "merged": True,
            "message": "Pull Request successfully merged",
            "sha": merge_sha,
        }

    def get_commit(self, *, owner, repo, sha):
        if owner != "acme" or repo != "payments":
            return None
        value = self.commits.get(sha)
        return self._copy(value) if value is not None else None


class LostAckGitHubApi(FakeGitHubPullRequestApi):
    def merge_pull_request(self, **kwargs):
        super().merge_pull_request(**kwargs)
        raise RuntimeMutationUncertain("connection reset after merge")


class OtherActorLostAckGitHubApi(FakeGitHubPullRequestApi):
    def merge_pull_request(self, **kwargs):
        self.merge_calls += 1
        self.last_merge = dict(kwargs)
        merge_sha = "merge-other"
        self.pull_request["state"] = "closed"
        self.pull_request["merged"] = True
        self.pull_request["merge_commit_sha"] = merge_sha
        self.commits[merge_sha] = {
            "sha": merge_sha,
            "commit": {"message": "merged by another controller"},
        }
        raise RuntimeMutationUncertain(
            "connection reset while another actor merged"
        )


def subject():
    return ResourceIdentity(
        provider="github",
        resource_uid="github:acme/payments#123",
        namespace="acme/payments",
        kind="PullRequest",
        name="123",
    )


def build_plan(api=None):
    api = api or FakeGitHubPullRequestApi()
    provider = GitHubPullRequestMergeProvider(api)
    observation = provider.observe(
        subject(),
        observer=OBSERVER,
        observed_at=NOW,
    )
    plan = provider.plan(
        observation,
        {
            "state": "merged",
            "head_sha": "abc123",
        },
        created_at=NOW + timedelta(seconds=1),
        merge_method="squash",
    )
    return api, provider, observation, plan


def test_plan_binds_exact_approved_head_sha():
    _, _, observation, plan = build_plan()

    assert observation.observed_version == "head-sha:abc123"
    assert plan.before == {
        "state": "open",
        "head_sha": "abc123",
    }
    assert plan.desired == {
        "state": "merged",
        "head_sha": "abc123",
    }
    assert plan.preconditions == {
        "observed_version": "head-sha:abc123",
        "approved_head_sha": "abc123",
    }


def test_merge_uses_head_cas_and_operation_ownership_marker():
    api, provider, _, plan = build_plan()

    receipt = provider.apply(
        plan,
        operation_id="op-123",
        actor=ACTOR,
        now=NOW + timedelta(seconds=2),
    )

    receipt.verify()
    assert receipt.details["plan_hash"] == plan.plan_hash
    assert api.merge_calls == 1
    assert api.last_merge["head_sha"] == "abc123"
    assert api.last_merge["merge_method"] == "squash"
    assert "agent-control-plane-operation:op-123" in (
        api.last_merge["commit_message"]
    )
    assert f"agent-control-plane-plan:{plan.plan_hash}" in (
        api.last_merge["commit_message"]
    )


def test_head_drift_is_rejected_before_merge_side_effect():
    api, provider, _, plan = build_plan()
    api.pull_request["head"]["sha"] = "def456"

    with pytest.raises(
        ProtocolViolation,
        match="head changed before execution",
    ):
        provider.apply(
            plan,
            operation_id="op-stale",
            actor=ACTOR,
            now=NOW + timedelta(seconds=2),
        )

    assert api.merge_calls == 0


def test_lost_ack_reconciles_when_commit_proves_operation_ownership():
    api = LostAckGitHubApi()
    api, provider, _, plan = build_plan(api)

    receipt = provider.apply(
        plan,
        operation_id="op-lost-ack",
        actor=ACTOR,
        now=NOW + timedelta(seconds=2),
    )

    assert api.merge_calls == 1
    assert receipt.details["verified_after_uncertain_mutation"] is True
    assert receipt.details["plan_hash"] == plan.plan_hash


def test_lost_ack_does_not_claim_another_actors_merge():
    api = OtherActorLostAckGitHubApi()
    api, provider, _, plan = build_plan(api)

    with pytest.raises(
        RuntimeMutationOwnershipUncertain,
        match="ambiguous after lost ACK",
    ):
        provider.apply(
            plan,
            operation_id="op-not-owner",
            actor=ACTOR,
            now=NOW + timedelta(seconds=2),
        )

    assert api.merge_calls == 1


def test_open_same_head_reconciles_as_not_applied():
    _, provider, _, plan = build_plan()
    fresh = provider.observe(
        subject(),
        observer=VERIFIER,
        observed_at=NOW + timedelta(seconds=3),
    )

    result = provider.reconcile(
        plan,
        attempt_id="attempt-1",
        operation_id="op-1",
        fresh_observation=fresh,
        reconciled_at=NOW + timedelta(seconds=3),
    )

    result.verify()
    assert result.status is ReconciliationStatus.NOT_APPLIED


def test_provider_receipt_is_not_outcome_proof():
    _, provider, _, plan = build_plan()

    receipt = provider.apply(
        plan,
        operation_id="op-verify",
        actor=ACTOR,
        now=NOW + timedelta(seconds=2),
    )
    after = provider.observe(
        subject(),
        observer=VERIFIER,
        observed_at=NOW + timedelta(seconds=3),
    )
    report = provider.verify_outcome(
        plan,
        operation_id="op-verify",
        observation=after,
        verifier=VERIFIER,
        verified_at=NOW + timedelta(seconds=3),
    )

    assert receipt.details["status"] == "APPLIED"
    assert report.succeeded
    assert receipt.receipt_hash != report.report_hash




@pytest.mark.parametrize(
    "message_case",
    ("operation_suffix", "plan_suffix", "operation_inline", "plan_inline"),
)
def test_ownership_markers_require_complete_lines(message_case):
    api, provider, _, plan = build_plan()
    operation_id = "op-ours"
    operation_marker = f"agent-control-plane-operation:{operation_id}"
    plan_marker = f"agent-control-plane-plan:{plan.plan_hash}"
    messages = {
        "operation_suffix": f"{operation_marker}-different\\n{plan_marker}",
        "plan_suffix": f"{operation_marker}\\n{plan_marker}-different",
        "operation_inline": f"quoted {operation_marker}\\n{plan_marker}",
        "plan_inline": f"{operation_marker}\\nquoted {plan_marker}",
    }
    api.pull_request["state"] = "closed"
    api.pull_request["merged"] = True
    api.pull_request["merge_commit_sha"] = "merge-collision"
    api.commits["merge-collision"] = {
        "sha": "merge-collision",
        "commit": {"message": messages[message_case]},
    }
    fresh = provider.observe(
        subject(), observer=VERIFIER,
        observed_at=NOW + timedelta(seconds=3),
    )
    result = provider.reconcile(
        plan,
        attempt_id="attempt-marker-collision",
        operation_id=operation_id,
        fresh_observation=fresh,
        reconciled_at=NOW + timedelta(seconds=3),
    )
    report = provider.verify_outcome(
        plan, operation_id=operation_id, observation=fresh,
        verifier=VERIFIER, verified_at=NOW + timedelta(seconds=3),
    )

    assert result.status is ReconciliationStatus.UNKNOWN
    assert not report.succeeded
    with pytest.raises(RuntimeMutationOwnershipUncertain, match="ownership is unknown"):
        provider.apply(
            plan, operation_id=operation_id,
            actor=ACTOR, now=NOW + timedelta(seconds=4),
        )


def test_merge_by_other_actor_is_unknown_even_if_desired_state_exists():
    api = FakeGitHubPullRequestApi()
    _, provider, _, plan = build_plan(api)
    api.pull_request["state"] = "closed"
    api.pull_request["merged"] = True
    api.pull_request["merge_commit_sha"] = "merge-other"
    api.commits["merge-other"] = {
        "sha": "merge-other",
        "commit": {"message": "other actor"},
    }
    fresh = provider.observe(
        subject(),
        observer=VERIFIER,
        observed_at=NOW + timedelta(seconds=3),
    )

    result = provider.reconcile(
        plan,
        attempt_id="attempt-unknown",
        operation_id="op-ours",
        fresh_observation=fresh,
        reconciled_at=NOW + timedelta(seconds=3),
    )

    assert result.status is ReconciliationStatus.UNKNOWN
