from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol

from .provable_execution import (
    ObservationSnapshot,
    ProviderReceipt,
    ReconciliationResult,
    ReconciliationStatus,
    TransitionPlan,
    VerificationCondition,
    VerificationReport,
    VerificationStatus,
)
from .runtime_clients import (
    RuntimeMutationOwnershipUncertain,
    RuntimeMutationUncertain,
)
from .state_transition_protocol import (
    Principal,
    ProtocolViolation,
    ResourceIdentity,
)


_OPERATION_MARKER_PREFIX = "agent-control-plane-operation:"
_PLAN_MARKER_PREFIX = "agent-control-plane-plan:"


class GitHubPullRequestApi(Protocol):
    def get_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
    ) -> Mapping[str, Any] | None:
        ...

    def merge_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        head_sha: str,
        merge_method: str,
        commit_message: str,
    ) -> Mapping[str, Any]:
        ...

    def get_commit(
        self,
        *,
        owner: str,
        repo: str,
        sha: str,
    ) -> Mapping[str, Any] | None:
        ...


def _repo(subject: ResourceIdentity) -> tuple[str, str]:
    if subject.provider != "github":
        raise ProtocolViolation("pull request provider requires github subject")
    if subject.kind != "PullRequest":
        raise ProtocolViolation(
            "pull request provider requires PullRequest subject"
        )
    parts = subject.namespace.split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise ProtocolViolation(
            "github pull request namespace must be owner/repo"
        )
    return parts[0], parts[1]


def _number(subject: ResourceIdentity) -> int:
    try:
        number = int(subject.name)
    except ValueError as exc:
        raise ProtocolViolation(
            "github pull request name must be numeric"
        ) from exc
    if number <= 0:
        raise ProtocolViolation(
            "github pull request number must be positive"
        )
    return number


def _head_sha(pull_request: Mapping[str, Any]) -> str:
    head = pull_request.get("head")
    if not isinstance(head, Mapping):
        raise ProtocolViolation("github pull request head is required")
    value = head.get("sha")
    if not isinstance(value, str) or not value:
        raise ProtocolViolation("github pull request head SHA is required")
    return value


def _state(pull_request: Mapping[str, Any]) -> str:
    value = pull_request.get("state")
    if not isinstance(value, str) or not value:
        raise ProtocolViolation("github pull request state is required")
    return value


def _merged(pull_request: Mapping[str, Any]) -> bool:
    value = pull_request.get("merged", False)
    if not isinstance(value, bool):
        raise ProtocolViolation("github pull request merged must be boolean")
    return value


def _merge_commit_sha(
    pull_request: Mapping[str, Any],
) -> str | None:
    value = pull_request.get("merge_commit_sha")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ProtocolViolation(
            "github pull request merge_commit_sha must be string or null"
        )
    return value


def _commit_message(commit: Mapping[str, Any]) -> str:
    nested = commit.get("commit")
    if isinstance(nested, Mapping):
        value = nested.get("message")
    else:
        value = commit.get("message")
    if not isinstance(value, str):
        return ""
    return value


def _operation_marker(operation_id: str) -> str:
    return f"{_OPERATION_MARKER_PREFIX}{operation_id}"


def _plan_marker(plan_hash: str) -> str:
    return f"{_PLAN_MARKER_PREFIX}{plan_hash}"


def _owned_commit_message(
    *,
    operation_id: str,
    plan_hash: str,
) -> str:
    return (
        f"{_operation_marker(operation_id)}\n"
        f"{_plan_marker(plan_hash)}"
    )


def _message_owns_operation(
    message: str,
    *,
    operation_id: str,
    plan_hash: str,
) -> bool:
    # Commit messages are line-oriented evidence. Substring matching can
    # confuse op-123 with op-123-extra, or a hash with its longer prefix.
    # This rejects accidental marker collisions; it does not authenticate
    # the writer of a matching commit message.
    lines = set(message.splitlines())
    return (
        _operation_marker(operation_id) in lines
        and _plan_marker(plan_hash) in lines
    )


class GitHubPullRequestMergeProvider:
    """Provider-neutral proof slice for one GitHub PR merge side effect."""

    def __init__(self, api: GitHubPullRequestApi) -> None:
        self.api = api

    def observe(
        self,
        subject: ResourceIdentity,
        *,
        observer: Principal,
        observed_at: datetime,
    ) -> ObservationSnapshot:
        owner, repo = _repo(subject)
        number = _number(subject)
        pull_request = self.api.get_pull_request(
            owner=owner,
            repo=repo,
            number=number,
        )
        if pull_request is None:
            raise ProtocolViolation("github pull request does not exist")

        head_sha = _head_sha(pull_request)
        merge_commit_sha = _merge_commit_sha(pull_request)
        commit_message = None
        if merge_commit_sha is not None:
            commit = self.api.get_commit(
                owner=owner,
                repo=repo,
                sha=merge_commit_sha,
            )
            if commit is not None:
                commit_message = _commit_message(commit)

        return ObservationSnapshot.capture(
            subject=subject,
            observed_version=f"head-sha:{head_sha}",
            observed_at=observed_at,
            state={
                "state": _state(pull_request),
                "merged": _merged(pull_request),
                "head_sha": head_sha,
                "merge_commit_sha": merge_commit_sha,
                "merge_commit_message": commit_message,
            },
            observer=observer,
        )

    def plan(
        self,
        observation: ObservationSnapshot,
        desired: Mapping[str, Any],
        *,
        created_at: datetime,
        merge_method: str = "squash",
    ) -> TransitionPlan:
        observation.verify()
        if observation.subject.provider != "github":
            raise ProtocolViolation("github merge plan requires github subject")
        if observation.subject.kind != "PullRequest":
            raise ProtocolViolation(
                "github merge plan requires PullRequest subject"
            )
        if merge_method not in {"merge", "squash"}:
            raise ProtocolViolation(
                "github merge provider supports merge or squash"
            )

        state = observation.state
        head_sha = state.get("head_sha")
        if not isinstance(head_sha, str) or not head_sha:
            raise ProtocolViolation(
                "github merge plan requires observed head SHA"
            )
        if state.get("merged") is not False or state.get("state") != "open":
            raise ProtocolViolation(
                "github merge plan requires an open unmerged pull request"
            )

        desired_state = dict(desired)
        if desired_state != {
            "state": "merged",
            "head_sha": head_sha,
        }:
            raise ProtocolViolation(
                "github merge desired state must preserve approved head SHA"
            )

        return TransitionPlan.seal(
            plan_id=(
                f"github-merge:{observation.subject.resource_uid}:{head_sha}"
            ),
            observation=observation,
            before={
                "state": "open",
                "head_sha": head_sha,
            },
            desired=desired_state,
            provider="github",
            operation="merge_pull_request",
            parameters={"merge_method": merge_method},
            preconditions={
                "observed_version": observation.observed_version,
                "approved_head_sha": head_sha,
            },
            created_at=created_at,
        )

    def _validate_plan(
        self,
        plan: TransitionPlan,
    ) -> tuple[str, str, int, str, str]:
        plan.verify()
        owner, repo = _repo(plan.subject)
        number = _number(plan.subject)
        if plan.provider != "github":
            raise ProtocolViolation("github merge plan provider mismatch")
        if plan.operation != "merge_pull_request":
            raise ProtocolViolation("github merge operation mismatch")

        head_sha = plan.preconditions.get("approved_head_sha")
        if not isinstance(head_sha, str) or not head_sha:
            raise ProtocolViolation(
                "github merge plan approved_head_sha is required"
            )
        if plan.before != {
            "state": "open",
            "head_sha": head_sha,
        }:
            raise ProtocolViolation("github merge before-state mismatch")
        if plan.desired != {
            "state": "merged",
            "head_sha": head_sha,
        }:
            raise ProtocolViolation("github merge desired-state mismatch")

        merge_method = plan.parameters.get("merge_method")
        if merge_method not in {"merge", "squash"}:
            raise ProtocolViolation(
                "github merge method must be merge or squash"
            )
        return owner, repo, number, head_sha, str(merge_method)

    def _validate_live_precondition(
        self,
        *,
        plan: TransitionPlan,
        live: ObservationSnapshot,
    ) -> None:
        live.verify()
        if live.subject != plan.subject:
            raise ProtocolViolation(
                "github merge live subject does not match plan"
            )
        approved_head = plan.preconditions["approved_head_sha"]
        if live.state.get("head_sha") != approved_head:
            raise ProtocolViolation(
                "github pull request head changed before execution"
            )
        if (
            live.observed_version
            != plan.preconditions.get("observed_version")
        ):
            raise ProtocolViolation(
                "github pull request version changed before execution"
            )
        if live.state.get("merged") is not False:
            raise ProtocolViolation(
                "github pull request is already merged"
            )
        if live.state.get("state") != "open":
            raise ProtocolViolation(
                "github pull request is no longer open"
            )

    def apply(
        self,
        plan: TransitionPlan,
        *,
        operation_id: str,
        actor: Principal,
        now: datetime,
    ) -> ProviderReceipt:
        owner, repo, number, head_sha, merge_method = (
            self._validate_plan(plan)
        )

        live = self.observe(
            plan.subject,
            observer=actor,
            observed_at=now,
        )

        if live.state.get("merged") is True:
            message = str(
                live.state.get("merge_commit_message") or ""
            )
            if _message_owns_operation(
                message,
                operation_id=operation_id,
                plan_hash=plan.plan_hash,
            ):
                return ProviderReceipt.capture(
                    operation_id=operation_id,
                    provider="github",
                    received_at=now,
                    details={
                        "status": "APPLIED",
                        "replayed": True,
                        "plan_hash": plan.plan_hash,
                        "merge_commit_sha": live.state.get(
                            "merge_commit_sha"
                        ),
                    },
                )
            raise RuntimeMutationOwnershipUncertain(
                "pull request is merged but operation ownership is unknown",
                resource_ref=(
                    f"github://{owner}/{repo}/pull/{number}"
                ),
                operation_id=operation_id,
                observed_operation_id=None,
            )

        self._validate_live_precondition(plan=plan, live=live)
        commit_message = _owned_commit_message(
            operation_id=operation_id,
            plan_hash=plan.plan_hash,
        )

        try:
            response = self.api.merge_pull_request(
                owner=owner,
                repo=repo,
                number=number,
                head_sha=head_sha,
                merge_method=merge_method,
                commit_message=commit_message,
            )
        except RuntimeMutationUncertain as exc:
            observed = self.observe(
                plan.subject,
                observer=actor,
                observed_at=now,
            )
            result = self.reconcile(
                plan,
                attempt_id=operation_id,
                operation_id=operation_id,
                fresh_observation=observed,
                reconciled_at=now,
            )
            if result.status is not ReconciliationStatus.APPLIED:
                raise RuntimeMutationOwnershipUncertain(
                    "github merge outcome is ambiguous after lost ACK",
                    resource_ref=(
                        f"github://{owner}/{repo}/pull/{number}"
                    ),
                    operation_id=operation_id,
                    observed_operation_id=None,
                ) from exc
            return ProviderReceipt.capture(
                operation_id=operation_id,
                provider="github",
                received_at=now,
                details={
                    "status": "APPLIED",
                    "replayed": False,
                    "verified_after_uncertain_mutation": True,
                    "plan_hash": plan.plan_hash,
                    "merge_commit_sha": observed.state.get(
                        "merge_commit_sha"
                    ),
                },
            )

        merged = response.get("merged")
        merge_commit_sha = response.get("sha")
        if merged is not True:
            raise ProtocolViolation(
                "github merge acknowledgement did not confirm merged=true"
            )
        if not isinstance(merge_commit_sha, str) or not merge_commit_sha:
            raise ProtocolViolation(
                "github merge acknowledgement lacks merge commit SHA"
            )

        return ProviderReceipt.capture(
            operation_id=operation_id,
            provider="github",
            received_at=now,
            details={
                "status": "APPLIED",
                "replayed": False,
                "plan_hash": plan.plan_hash,
                "merge_commit_sha": merge_commit_sha,
            },
        )

    def reconcile(
        self,
        plan: TransitionPlan,
        *,
        attempt_id: str,
        operation_id: str,
        fresh_observation: ObservationSnapshot,
        reconciled_at: datetime,
    ) -> ReconciliationResult:
        self._validate_plan(plan)
        fresh_observation.verify()
        if fresh_observation.subject != plan.subject:
            raise ProtocolViolation(
                "github reconcile observation subject mismatch"
            )

        approved_head = plan.preconditions["approved_head_sha"]
        observed_head = fresh_observation.state.get("head_sha")
        merged = fresh_observation.state.get("merged")
        state = fresh_observation.state.get("state")

        if (
            merged is True
            and observed_head == approved_head
        ):
            message = str(
                fresh_observation.state.get(
                    "merge_commit_message"
                )
                or ""
            )
            if _message_owns_operation(
                message,
                operation_id=operation_id,
                plan_hash=plan.plan_hash,
            ):
                return ReconciliationResult.seal(
                    status=ReconciliationStatus.APPLIED,
                    attempt_id=attempt_id,
                    operation_id=operation_id,
                    observed_after=fresh_observation,
                    reason="exact merge ownership proven from commit message",
                    details={
                        "plan_hash": plan.plan_hash,
                        "merge_commit_sha": fresh_observation.state.get(
                            "merge_commit_sha"
                        ),
                    },
                    reconciled_at=reconciled_at,
                )

        if (
            merged is False
            and state == "open"
            and observed_head == approved_head
        ):
            return ReconciliationResult.seal(
                status=ReconciliationStatus.NOT_APPLIED,
                attempt_id=attempt_id,
                operation_id=operation_id,
                observed_after=fresh_observation,
                reason="pull request still matches approved precondition",
                details={
                    "plan_hash": plan.plan_hash,
                    "head_sha": observed_head,
                },
                reconciled_at=reconciled_at,
            )

        return ReconciliationResult.seal(
            status=ReconciliationStatus.UNKNOWN,
            attempt_id=attempt_id,
            operation_id=operation_id,
            observed_after=fresh_observation,
            reason="provider state cannot prove merge ownership",
            details={
                "plan_hash": plan.plan_hash,
                "observed_head_sha": observed_head,
                "merged": merged,
                "state": state,
                "merge_commit_sha": fresh_observation.state.get(
                    "merge_commit_sha"
                ),
            },
            reconciled_at=reconciled_at,
        )

    def verify_outcome(
        self,
        plan: TransitionPlan,
        *,
        operation_id: str,
        observation: ObservationSnapshot,
        verifier: Principal,
        verified_at: datetime,
    ) -> VerificationReport:
        self._validate_plan(plan)
        observation.verify()
        if observation.subject != plan.subject:
            raise ProtocolViolation(
                "github verification observation subject mismatch"
            )

        approved_head = plan.preconditions["approved_head_sha"]
        desired_reached = (
            observation.state.get("merged") is True
            and observation.state.get("head_sha") == approved_head
        )
        message = str(
            observation.state.get("merge_commit_message") or ""
        )
        ownership_proven = _message_owns_operation(
            message,
            operation_id=operation_id,
            plan_hash=plan.plan_hash,
        )

        conditions = (
            VerificationCondition(
                condition_type="DesiredStateReached",
                status=(
                    VerificationStatus.TRUE
                    if desired_reached
                    else VerificationStatus.FALSE
                ),
                reason=(
                    "MergedApprovedHead"
                    if desired_reached
                    else "DesiredMergeNotObserved"
                ),
                message=(
                    "approved pull request head is merged"
                    if desired_reached
                    else "approved merge postcondition is not satisfied"
                ),
                evidence_digest=observation.observation_hash,
            ),
            VerificationCondition(
                condition_type="OperationOwnershipProven",
                status=(
                    VerificationStatus.TRUE
                    if ownership_proven
                    else VerificationStatus.UNKNOWN
                ),
                reason=(
                    "CommitMarkerMatches"
                    if ownership_proven
                    else "CommitMarkerMissing"
                ),
                message=(
                    "merge commit binds operation and plan"
                    if ownership_proven
                    else "merge ownership cannot be proven"
                ),
                evidence_digest=observation.observation_hash,
            ),
        )
        return VerificationReport.seal(
            plan=plan,
            after_observation=observation,
            conditions=conditions,
            verifier=verifier,
            verified_at=verified_at,
        )
