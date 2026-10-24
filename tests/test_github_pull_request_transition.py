import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_control_plane.execution_journal import (
    ExecutionAttemptState,
    SQLiteExecutionJournal,
)
from agent_control_plane.github_pull_request_lifecycle import (
    GitHubPullRequestExecutionCoordinator,
)
from agent_control_plane.github_pull_request_transition import (
    GitHubPullRequestMergeProvider,
)
from agent_control_plane.live_effect_boundary import (
    CurrentEffectAuthority,
    EffectScope,
    LiveEffectAdmissionDenied,
    LiveEffectBoundaryGate,
)
from agent_control_plane.provable_execution import (
    PlanAuthorizationBinding,
    PlanExecutionFence,
    ReconciliationStatus,
    VerificationStatus,
)
from agent_control_plane.runtime_clients import (
    RuntimeMutationOwnershipUncertain,
    RuntimeMutationUncertain,
)
from agent_control_plane.state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    ExecutionLease,
    Principal,
    ProtocolViolation,
    ResourceIdentity,
    StateTransition,
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





class GitHubAuthorityReaderFixture:
    """Test fake for the current-authority row; production uses an operator-owned reader."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def read_current(self, **kwargs):
        self.calls.append(kwargs)
        return self.snapshot


def _authorized_apply(
    provider, plan, *, operation_id, actor, now,
    authority_approval_state="ACTIVE",
    action_parameters=None,
    caller=None,
):
    """Exercise the merge through the same current-authority gate as other effects."""
    caller = actor if caller is None else caller
    transition = StateTransition.seal(
        transition_id=f"github-transition-{operation_id}",
        subject=plan.subject,
        expected_generation=1,
        before=plan.before,
        desired=plan.desired,
        evidence_hash="sha256:" + "a" * 64,
        outcome_contract_hash="sha256:" + "b" * 64,
        created_at=NOW,
    )
    action = ActionIntent.seal(
        action_id=f"github-action-{operation_id}",
        transition_hash=transition.transition_hash,
        provider=plan.provider,
        operation=plan.operation,
        parameters=(
            plan.parameters if action_parameters is None else action_parameters
        ),
    )
    authorization = AuthorizationBinding.seal(
        transition=transition,
        action=action,
        policy_version="github-test-policy/v1",
        policy_decision_hash="sha256:" + "d" * 64,
        approval_hash="sha256:" + "c" * 64,
        principal=actor,
        expires_at=NOW + timedelta(minutes=5),
    )
    active_lease = ExecutionLease(
        lease_id=f"github-lease-{operation_id}",
        resource_uid=plan.subject.resource_uid,
        holder=actor,
        epoch=1,
        acquired_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    policy_input_hash = "sha256:" + "e" * 64
    scope = EffectScope(
        tenant_id="github-test-tenant",
        project_id="acme/payments",
        cluster_id="github-adapter-test",
        nonce=f"github-nonce-{operation_id}",
    )
    snapshot = CurrentEffectAuthority(
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        cluster_id=scope.cluster_id,
        work_id="github-pr-work",
        operation_id=operation_id,
        nonce=scope.nonce,
        resource_uid=plan.subject.resource_uid,
        principal=authorization.principal,
        executor=actor,
        resource_generation=transition.expected_generation,
        lease_id=active_lease.lease_id,
        lease_epoch=active_lease.epoch,
        transition_hash=transition.transition_hash,
        action_hash=action.action_hash,
        authorization_hash=authorization.authorization_hash,
        approval_hash=authorization.approval_hash,
        policy_version=authorization.policy_version,
        policy_input_hash=policy_input_hash,
        policy_decision_hash=authorization.policy_decision_hash,
        policy_effect="PERMIT",
        approval_state=authority_approval_state,
        principal_state="ACTIVE",
        operation_state="ACTIVE",
        observed_at=now,
        valid_until=now + timedelta(minutes=1),
        version=1,
    )
    reader = GitHubAuthorityReaderFixture(snapshot)
    provider._effect_gate = LiveEffectBoundaryGate(
        scope=scope,
        reader=reader,
        clock=lambda: now,
    )
    return provider.apply(
        plan,
        operation_id=operation_id,
        actor=actor,
        now=now,
        transition=transition,
        action=action,
        authorization=authorization,
        active_lease=active_lease,
        caller=caller,
        work_id="github-pr-work",
        policy_input_hash=policy_input_hash,
    )


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

    receipt = _authorized_apply(
        provider, plan, operation_id="op-123", actor=ACTOR,
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
        _authorized_apply(
            provider, plan, operation_id="op-stale", actor=ACTOR,
            now=NOW + timedelta(seconds=2),
        )

    assert api.merge_calls == 0



def test_merged_matching_markers_with_changed_head_cannot_replay_as_owned():
    api, provider, _, plan = build_plan()
    operation_id = "op-frozen-head"
    merge_sha = "merge-different-head"
    api.pull_request["head"]["sha"] = "def456"
    api.pull_request["state"] = "closed"
    api.pull_request["merged"] = True
    api.pull_request["merge_commit_sha"] = merge_sha
    api.commits[merge_sha] = {
        "sha": merge_sha,
        "commit": {
            "message": (
                f"agent-control-plane-operation:{operation_id}\n"
                f"agent-control-plane-plan:{plan.plan_hash}"
            ),
        },
    }
    fresh = provider.observe(
        subject(), observer=VERIFIER,
        observed_at=NOW + timedelta(seconds=3),
    )
    reconciliation = provider.reconcile(
        plan, attempt_id="attempt-stale-head",
        operation_id=operation_id, fresh_observation=fresh,
        reconciled_at=NOW + timedelta(seconds=3),
    )
    report = provider.verify_outcome(
        plan, operation_id=operation_id, observation=fresh,
        verifier=VERIFIER, verified_at=NOW + timedelta(seconds=3),
    )
    assert reconciliation.status is ReconciliationStatus.UNKNOWN
    assert not report.succeeded
    ownership = next(
        c.status for c in report.conditions
        if c.condition_type == "OperationOwnershipProven"
    )
    assert ownership is VerificationStatus.UNKNOWN
    with pytest.raises(RuntimeMutationOwnershipUncertain, match="ownership is unknown"):
        _authorized_apply(
            provider, plan, operation_id=operation_id, actor=ACTOR,
            now=NOW + timedelta(seconds=4),
        )
    assert api.merge_calls == 0

def test_lost_ack_reconciles_when_commit_proves_operation_ownership():
    api = LostAckGitHubApi()
    api, provider, _, plan = build_plan(api)

    receipt = _authorized_apply(
        provider, plan, operation_id="op-lost-ack", actor=ACTOR,
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
        _authorized_apply(
            provider, plan, operation_id="op-not-owner", actor=ACTOR,
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

    receipt = _authorized_apply(
        provider, plan, operation_id="op-verify", actor=ACTOR,
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
    (
        "operation_suffix", "plan_suffix",
        "operation_inline", "plan_inline",
        "operation_competing", "plan_competing",
        "operation_duplicate", "plan_duplicate",
    ),
)
def test_ownership_markers_require_complete_lines(message_case):
    api, provider, _, plan = build_plan()
    operation_id = "op-ours"
    operation_marker = f"agent-control-plane-operation:{operation_id}"
    plan_marker = f"agent-control-plane-plan:{plan.plan_hash}"
    messages = {
        "operation_suffix": f"{operation_marker}-different\n{plan_marker}",
        "plan_suffix": f"{operation_marker}\n{plan_marker}-different",
        "operation_inline": f"quoted {operation_marker}\n{plan_marker}",
        "plan_inline": f"{operation_marker}\nquoted {plan_marker}",
        "operation_competing": (
            f"{operation_marker}\nagent-control-plane-operation:op-other\n{plan_marker}"
        ),
        "plan_competing": (
            f"{operation_marker}\n{plan_marker}\nagent-control-plane-plan:other-plan"
        ),
        "operation_duplicate": f"{operation_marker}\n{operation_marker}\n{plan_marker}",
        "plan_duplicate": f"{operation_marker}\n{plan_marker}\n{plan_marker}",
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
        _authorized_apply(
            provider, plan, operation_id=operation_id,
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



def test_direct_merge_without_current_authority_gate_is_denied():
    api, provider, _, plan = build_plan()

    with pytest.raises(
        ProtocolViolation,
        match="current effect authority gate is required",
    ):
        provider.apply(
            plan,
            operation_id="op-untrusted-direct-call",
            actor=ACTOR,
            now=NOW + timedelta(seconds=2),
        )

    assert api.merge_calls == 0



def test_revoked_current_approval_denies_before_github_merge():
    api, provider, _, plan = build_plan()

    with pytest.raises(LiveEffectAdmissionDenied, match="revoked or denied"):
        _authorized_apply(
            provider, plan,
            operation_id="op-revoked",
            actor=ACTOR,
            now=NOW + timedelta(seconds=2),
            authority_approval_state="REVOKED",
        )

    assert api.merge_calls == 0


def test_action_parameter_mismatch_denies_before_github_merge():
    api, provider, _, plan = build_plan()

    with pytest.raises(LiveEffectAdmissionDenied, match="exact-plan execution admission"):
        _authorized_apply(
            provider, plan,
            operation_id="op-parameter-mismatch",
            actor=ACTOR,
            now=NOW + timedelta(seconds=2),
            action_parameters={"merge_method": "merge"},
        )

    assert api.merge_calls == 0



def test_executor_substitution_denies_before_github_merge():
    api, provider, _, plan = build_plan()
    substituted = Principal(type="controller", subject="github-controller-other")

    with pytest.raises(
        LiveEffectAdmissionDenied,
        match="actor must match the lease-holding caller",
    ):
        _authorized_apply(
            provider, plan,
            operation_id="op-executor-substitution",
            actor=ACTOR,
            caller=substituted,
            now=NOW + timedelta(seconds=2),
        )

    assert api.merge_calls == 0



def _github_lifecycle_fixture(tmp_path, *, api=None, approval_state="ACTIVE", journal=None):
    api, provider, _, plan = build_plan(api)
    transition = StateTransition.seal(
        transition_id="github-lifecycle-transition",
        subject=plan.subject,
        expected_generation=1,
        before=plan.before,
        desired=plan.desired,
        evidence_hash="sha256:" + "a" * 64,
        outcome_contract_hash="sha256:" + "b" * 64,
        created_at=NOW,
    )
    action = ActionIntent.seal(
        action_id="github-lifecycle-action",
        transition_hash=transition.transition_hash,
        provider=plan.provider,
        operation=plan.operation,
        parameters=plan.parameters,
    )
    authorization = AuthorizationBinding.seal(
        transition=transition,
        action=action,
        policy_version="github-test-policy/v1",
        policy_decision_hash="sha256:" + "d" * 64,
        approval_hash="sha256:" + "c" * 64,
        principal=ACTOR,
        expires_at=NOW + timedelta(minutes=5),
    )
    active_lease = ExecutionLease(
        lease_id="github-lifecycle-lease",
        resource_uid=plan.subject.resource_uid,
        holder=ACTOR,
        epoch=1,
        acquired_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    plan_authorization = PlanAuthorizationBinding.derive(
        plan=plan, transition=transition, action=action,
        authorization=authorization,
    )
    plan_fence = PlanExecutionFence.bind(
        plan=plan, authorization=plan_authorization, lease=active_lease,
    )
    journal = journal or SQLiteExecutionJournal(tmp_path / "github-lifecycle.db")
    attempt = journal.prepare_plan(
        plan=plan, authorization=plan_authorization, fence=plan_fence,
        prepared_at=NOW + timedelta(seconds=1),
    )
    scope = EffectScope(
        tenant_id="github-test-tenant",
        project_id="acme/payments",
        cluster_id="github-lifecycle-test",
        nonce=f"github-lifecycle-nonce-{attempt.operation_id}",
    )
    policy_input_hash = "sha256:" + "e" * 64
    snapshot = CurrentEffectAuthority(
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        cluster_id=scope.cluster_id,
        work_id="github-pr-work",
        operation_id=attempt.operation_id,
        nonce=scope.nonce,
        resource_uid=plan.subject.resource_uid,
        principal=authorization.principal,
        executor=ACTOR,
        resource_generation=transition.expected_generation,
        lease_id=active_lease.lease_id,
        lease_epoch=active_lease.epoch,
        transition_hash=transition.transition_hash,
        action_hash=action.action_hash,
        authorization_hash=authorization.authorization_hash,
        approval_hash=authorization.approval_hash,
        policy_version=authorization.policy_version,
        policy_input_hash=policy_input_hash,
        policy_decision_hash=authorization.policy_decision_hash,
        policy_effect="PERMIT",
        approval_state=approval_state,
        principal_state="ACTIVE",
        operation_state="ACTIVE",
        observed_at=NOW + timedelta(seconds=2),
        valid_until=NOW + timedelta(minutes=1),
        version=1,
    )
    provider._effect_gate = LiveEffectBoundaryGate(
        scope=scope, reader=GitHubAuthorityReaderFixture(snapshot),
        clock=lambda: NOW + timedelta(seconds=2),
    )
    return {
        "api": api, "provider": provider, "plan": plan,
        "transition": transition, "action": action,
        "authorization": authorization, "active_lease": active_lease,
        "journal": journal, "attempt": attempt,
        "coordinator": GitHubPullRequestExecutionCoordinator(
            provider=provider, journal=journal,
            clock=lambda: NOW + timedelta(seconds=2),
        ),
        "policy_input_hash": policy_input_hash,
    }


def _execute_github_lifecycle(fixture):
    return fixture["coordinator"].execute(
        plan=fixture["plan"],
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        active_lease=fixture["active_lease"],
        actor=ACTOR,
        caller=ACTOR,
        work_id="github-pr-work",
        policy_input_hash=fixture["policy_input_hash"],
    )


def test_github_lifecycle_persists_before_effect_and_commits_after_observation(tmp_path):
    fixture = _github_lifecycle_fixture(tmp_path)
    result = _execute_github_lifecycle(fixture)
    assert result.attempt.state is ExecutionAttemptState.COMMITTED
    assert result.attempt.operation_id == fixture["attempt"].operation_id
    assert result.receipt is not None
    assert result.reconciliation.status is ReconciliationStatus.APPLIED
    assert result.attempt.result["provider"] == "github"
    assert result.attempt.result["plan_hash"] == fixture["plan"].plan_hash
    assert result.attempt.result["provider_receipt"]["receipt_hash"] == result.receipt.receipt_hash
    assert fixture["api"].merge_calls == 1
    assert fixture["journal"].get(result.attempt.attempt_id) == result.attempt


class FailOnceGitHubJournal(SQLiteExecutionJournal):
    def __init__(self, path):
        super().__init__(path)
        self.fail_next_commit = True

    def commit(self, attempt, *, completed_at, result):
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("simulated crash before journal commit")
        return super().commit(attempt, completed_at=completed_at, result=result)


def test_github_lifecycle_recovers_merge_after_commit_crash_without_resend(tmp_path):
    journal = FailOnceGitHubJournal(tmp_path / "github-recovery.db")
    fixture = _github_lifecycle_fixture(tmp_path, journal=journal)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _execute_github_lifecycle(fixture)
    prepared = journal.get(fixture["attempt"].attempt_id)
    assert prepared is not None and prepared.state is ExecutionAttemptState.PREPARED
    assert fixture["api"].merge_calls == 1

    recovered = _execute_github_lifecycle(fixture)
    assert recovered.attempt.state is ExecutionAttemptState.COMMITTED
    assert recovered.receipt is None
    assert recovered.attempt.result["recovered_from_provider_state"] is True
    assert recovered.attempt.result["reconciliation"]["status"] == "APPLIED"
    assert fixture["api"].merge_calls == 1


def test_github_lifecycle_denied_authority_is_durably_aborted(tmp_path):
    fixture = _github_lifecycle_fixture(tmp_path, approval_state="REVOKED")
    with pytest.raises(LiveEffectAdmissionDenied):
        _execute_github_lifecycle(fixture)
    attempt = fixture["journal"].get(fixture["attempt"].attempt_id)
    assert attempt is not None and attempt.state is ExecutionAttemptState.ABORTED
    assert attempt.result["status"] == "NOT_APPLIED"
    assert fixture["api"].merge_calls == 0


def test_github_lifecycle_marks_foreign_lost_ack_unknown_and_blocks_replay(tmp_path):
    fixture = _github_lifecycle_fixture(tmp_path, api=OtherActorLostAckGitHubApi())
    with pytest.raises(RuntimeMutationOwnershipUncertain):
        _execute_github_lifecycle(fixture)
    attempt = fixture["journal"].get(fixture["attempt"].attempt_id)
    assert attempt is not None and attempt.state is ExecutionAttemptState.UNKNOWN
    assert fixture["api"].merge_calls == 1
    with pytest.raises(ProtocolViolation, match="UNKNOWN prior attempt"):
        _execute_github_lifecycle(fixture)
