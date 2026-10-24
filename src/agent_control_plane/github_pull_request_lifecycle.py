from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .execution_journal import ExecutionAttempt, ExecutionAttemptState, SQLiteExecutionJournal
from .github_pull_request_transition import GitHubPullRequestMergeProvider
from .live_effect_boundary import LiveEffectAdmissionDenied
from .provable_execution import (
    PlanAuthorizationBinding,
    PlanExecutionFence,
    ProviderReceipt,
    ReconciliationResult,
    ReconciliationStatus,
    TransitionPlan,
    validate_plan_execution,
)
from .runtime_clients import RuntimeMutationOwnershipUncertain
from .state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    ExecutionLease,
    Principal,
    ProtocolViolation,
    StateTransition,
)

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class DurableGitHubPullRequestExecution:
    """Durable journal outcome; receipt is None when recovered by observation."""

    attempt: ExecutionAttempt
    receipt: ProviderReceipt | None
    reconciliation: ReconciliationResult


def _reconciliation_mapping(
    reconciliation: ReconciliationResult,
) -> dict[str, Any]:
    reconciliation.verify()
    return {
        "status": reconciliation.status.value,
        "attempt_id": reconciliation.attempt_id,
        "operation_id": reconciliation.operation_id,
        "observed_after_hash": reconciliation.observed_after_hash,
        "reason": reconciliation.reason,
        "details": dict(reconciliation.details),
        "reconciled_at": reconciliation.reconciled_at.isoformat(),
        "result_hash": reconciliation.result_hash,
        "result_version": reconciliation.result_version,
    }


def _receipt_mapping(receipt: ProviderReceipt) -> dict[str, Any]:
    receipt.verify()
    return {
        "operation_id": receipt.operation_id,
        "provider": receipt.provider,
        "received_at": receipt.received_at.isoformat(),
        "details": dict(receipt.details),
        "receipt_hash": receipt.receipt_hash,
        "receipt_version": receipt.receipt_version,
    }


class GitHubPullRequestExecutionCoordinator:
    """Write-ahead lifecycle around the guarded GitHub PR merge adapter.

    Reuses the provider-neutral ExecutionAttempt journal. A PREPARED attempt
    always reconciles against GitHub before another merge request is allowed.
    UNKNOWN is terminal for automatic execution; this coordinator never creates
    a second operation ID to get around an unresolved attempt.
    """

    def __init__(
        self,
        *,
        provider: GitHubPullRequestMergeProvider,
        journal: SQLiteExecutionJournal,
        clock: Clock,
    ) -> None:
        self.provider = provider
        self.journal = journal
        self.clock = clock

    def _reconcile(
        self,
        *,
        plan: TransitionPlan,
        attempt: ExecutionAttempt,
        actor: Principal,
    ) -> ReconciliationResult:
        observed = self.provider.observe(
            plan.subject,
            observer=actor,
            observed_at=self.clock(),
        )
        result = self.provider.reconcile(
            plan,
            attempt_id=attempt.attempt_id,
            operation_id=attempt.operation_id,
            fresh_observation=observed,
            reconciled_at=self.clock(),
        )
        result.verify()
        return result

    def _commit_applied(
        self,
        *,
        attempt: ExecutionAttempt,
        plan: TransitionPlan,
        plan_authorization: PlanAuthorizationBinding,
        plan_fence: PlanExecutionFence,
        reconciliation: ReconciliationResult,
        receipt: ProviderReceipt | None,
    ) -> DurableGitHubPullRequestExecution:
        if reconciliation.status is not ReconciliationStatus.APPLIED:
            raise ProtocolViolation(
                "GitHub execution can commit only after APPLIED reconciliation"
            )
        if reconciliation.operation_id != attempt.operation_id:
            raise ProtocolViolation(
                "GitHub reconciliation operation does not match journal"
            )
        result: dict[str, Any] = {
            "status": "APPLIED",
            "provider": "github",
            "operation": "merge_pull_request",
            "operation_id": attempt.operation_id,
            "attempt_id": attempt.attempt_id,
            "plan_hash": plan.plan_hash,
            "plan_authorization_hash": plan_authorization.binding_hash,
            "plan_fence_hash": plan_fence.fence_hash,
            "provider_receipt": (
                _receipt_mapping(receipt) if receipt is not None else None
            ),
            "recovered_from_provider_state": receipt is None,
            "reconciliation": _reconciliation_mapping(reconciliation),
        }
        committed = self.journal.commit(
            attempt,
            completed_at=self.clock(),
            result=result,
        )
        return DurableGitHubPullRequestExecution(
            attempt=committed,
            receipt=receipt,
            reconciliation=reconciliation,
        )

    def _mark_unknown(
        self,
        *,
        attempt: ExecutionAttempt,
        plan: TransitionPlan,
        reason: str,
        reconciliation: ReconciliationResult | None = None,
    ) -> ExecutionAttempt:
        current = self.journal.get(attempt.attempt_id)
        if current is None:
            raise ProtocolViolation("GitHub execution attempt disappeared")
        if current.state is not ExecutionAttemptState.PREPARED:
            return current
        result: dict[str, Any] = {
            "status": "UNKNOWN",
            "provider": "github",
            "operation": "merge_pull_request",
            "operation_id": current.operation_id,
            "attempt_id": current.attempt_id,
            "plan_hash": plan.plan_hash,
            "reason": reason,
        }
        if reconciliation is not None:
            result["reconciliation"] = _reconciliation_mapping(
                reconciliation
            )
        return self.journal.mark_unknown(
            current,
            completed_at=self.clock(),
            result=result,
        )

    def _raise_unknown(
        self,
        *,
        attempt: ExecutionAttempt,
        plan: TransitionPlan,
        reason: str,
        cause: BaseException | None = None,
        reconciliation: ReconciliationResult | None = None,
    ) -> None:
        self._mark_unknown(
            attempt=attempt,
            plan=plan,
            reason=reason,
            reconciliation=reconciliation,
        )
        error = RuntimeMutationOwnershipUncertain(
            reason,
            resource_ref=(
                f"github://{plan.subject.namespace}/pull/{plan.subject.name}"
            ),
            operation_id=attempt.operation_id,
            observed_operation_id=None,
        )
        if cause is not None:
            raise error from cause
        raise error

    def execute(
        self,
        *,
        plan: TransitionPlan,
        transition: StateTransition,
        action: ActionIntent,
        authorization: AuthorizationBinding,
        active_lease: ExecutionLease,
        actor: Principal,
        caller: Principal,
        work_id: str,
        policy_input_hash: str,
    ) -> DurableGitHubPullRequestExecution:
        """Prepare durably, reconcile first, then execute and independently verify."""

        plan.verify()
        transition.verify()
        action.verify()
        authorization.verify()
        plan_authorization = PlanAuthorizationBinding.derive(
            plan=plan,
            transition=transition,
            action=action,
            authorization=authorization,
        )
        plan_fence = PlanExecutionFence.bind(
            plan=plan,
            authorization=plan_authorization,
            lease=active_lease,
        )
        attempt = self.journal.prepare_plan(
            plan=plan,
            authorization=plan_authorization,
            fence=plan_fence,
            prepared_at=self.clock(),
        )

        try:
            validate_plan_execution(
                plan=plan,
                authorization=plan_authorization,
                fence=plan_fence,
                active_lease=active_lease,
                caller=caller,
                now=self.clock(),
            )
        except Exception as exc:
            self.journal.abort_not_applied(
                attempt,
                completed_at=self.clock(),
                result={
                    "status": "NOT_APPLIED",
                    "provider": "github",
                    "operation": "merge_pull_request",
                    "operation_id": attempt.operation_id,
                    "attempt_id": attempt.attempt_id,
                    "plan_hash": plan.plan_hash,
                    "reason": "exact-plan admission failed before provider call",
                    "error_type": type(exc).__name__,
                },
            )
            raise

        # Recovers a crash after GitHub applied the merge but before journal
        # terminalization. Ambiguous ownership is terminal UNKNOWN.
        before = self._reconcile(plan=plan, attempt=attempt, actor=actor)
        if before.status is ReconciliationStatus.APPLIED:
            return self._commit_applied(
                attempt=attempt, plan=plan,
                plan_authorization=plan_authorization, plan_fence=plan_fence,
                reconciliation=before, receipt=None,
            )
        if before.status is ReconciliationStatus.UNKNOWN:
            self._raise_unknown(
                attempt=attempt, plan=plan,
                reason="GitHub state cannot prove prior operation ownership",
                reconciliation=before,
            )

        try:
            receipt = self.provider.apply(
                plan,
                operation_id=attempt.operation_id,
                actor=actor,
                now=self.clock(),
                transition=transition,
                action=action,
                authorization=authorization,
                active_lease=active_lease,
                caller=caller,
                work_id=work_id,
                policy_input_hash=policy_input_hash,
            )
        except LiveEffectAdmissionDenied:
            self.journal.abort_not_applied(
                attempt,
                completed_at=self.clock(),
                result={
                    "status": "NOT_APPLIED",
                    "provider": "github",
                    "operation": "merge_pull_request",
                    "operation_id": attempt.operation_id,
                    "attempt_id": attempt.attempt_id,
                    "plan_hash": plan.plan_hash,
                    "reason": "current authority or live precondition denied",
                },
            )
            raise
        except Exception as exc:
            reconciliation = None
            try:
                reconciliation = self._reconcile(
                    plan=plan, attempt=attempt, actor=actor,
                )
            except Exception:
                # No independent observation is not evidence of failure.
                pass
            if (
                reconciliation is not None
                and reconciliation.status is ReconciliationStatus.APPLIED
            ):
                return self._commit_applied(
                    attempt=attempt, plan=plan,
                    plan_authorization=plan_authorization,
                    plan_fence=plan_fence,
                    reconciliation=reconciliation, receipt=None,
                )
            self._raise_unknown(
                attempt=attempt, plan=plan,
                reason="GitHub merge outcome is uncertain after provider invocation",
                cause=exc, reconciliation=reconciliation,
            )

        try:
            receipt.verify()
            if receipt.operation_id != attempt.operation_id or receipt.provider != "github":
                raise ProtocolViolation(
                    "GitHub provider receipt identity does not match attempt"
                )
            after = self._reconcile(plan=plan, attempt=attempt, actor=actor)
        except Exception as exc:
            self._raise_unknown(
                attempt=attempt, plan=plan,
                reason="GitHub merge lacks independent post-effect reconciliation",
                cause=exc,
            )

        if after.status is not ReconciliationStatus.APPLIED:
            self._raise_unknown(
                attempt=attempt, plan=plan,
                reason="GitHub post-effect observation cannot prove operation ownership",
                reconciliation=after,
            )
        return self._commit_applied(
            attempt=attempt, plan=plan,
            plan_authorization=plan_authorization, plan_fence=plan_fence,
            reconciliation=after, receipt=receipt,
        )
