from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from .authority_reservation import (
    AuthorityReservation,
    LeaseAuthority,
    SQLiteAuthorityReservationStore,
)
from .context_transition import (
    TransitionProposalBindingV3,
    build_execution_context_provenance,
)
from .execution_journal import (
    ExecutionAttempt,
    ExecutionAttemptState,
    SQLiteExecutionJournal,
)
from .kubernetes_deployment_transition import (
    ContextBoundExecutionContext,
    DeploymentScaleReceipt,
    KubernetesDeploymentScaleProvider,
)
from .provable_execution import (
    PlanAuthorizationBinding,
    PlanExecutionFence,
    TransitionPlan,
    validate_plan_execution,
)
from .state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    EvidenceBundle,
    ExecutionFence,
    ExecutionLease,
    OutcomeContract,
    Principal,
    ProtocolViolation,
    StateTransition,
)


Clock = Callable[[], datetime]


@dataclass(frozen=True)
class PreparedDeploymentExecution:
    attempt: ExecutionAttempt
    plan: TransitionPlan
    plan_authorization: PlanAuthorizationBinding
    plan_fence: PlanExecutionFence
    reservation: AuthorityReservation | None
    context_binding: ContextBoundExecutionContext


@dataclass(frozen=True)
class DurableDeploymentExecution:
    attempt: ExecutionAttempt
    receipt: DeploymentScaleReceipt
    reservation: AuthorityReservation | None


def _receipt_mapping(
    receipt: DeploymentScaleReceipt,
) -> dict[str, object]:
    return {
        "status": "APPLIED",
        "provider": "kubernetes",
        "operation": "scale_deployment",
        "resource_ref": receipt.resource_ref,
        "transition_hash": receipt.transition_hash,
        "action_hash": receipt.action_hash,
        "operation_id": receipt.operation_id,
        "desired_replicas": receipt.desired_replicas,
        "changed": receipt.changed,
        "replayed": receipt.replayed,
        "verified_after_uncertain_mutation": (
            receipt.verified_after_uncertain_mutation
        ),
        "before_resource_version": receipt.before_resource_version,
        "after_resource_version": receipt.after_resource_version,
        "before_generation": receipt.before_generation,
        "after_generation": receipt.after_generation,
        "plan_hash": receipt.plan_hash,
        "authority_reservation_hash": (
            receipt.authority_reservation_hash
        ),
    }


class KubernetesDeploymentExecutionCoordinator:
    """Thin lifecycle coordinator for one context-bound Deployment mutation.

    This object does not make policy or authority decisions. It only fixes the
    already-defined durability order:

        PREPARED
        -> acquire deterministic AuthorityReservation
        -> bind reservation to PREPARED journal row
        -> provider side effect
        -> COMMITTED journal result
        -> release reservation

    Any exception before terminal journal state deliberately leaves the attempt
    PREPARED and, for proposal v3, leaves authority frozen for replay/reconcile.
    """

    def __init__(
        self,
        *,
        provider: KubernetesDeploymentScaleProvider,
        journal: SQLiteExecutionJournal,
        reservation_store: SQLiteAuthorityReservationStore,
        clock: Clock,
        lease_authority: LeaseAuthority | None = None,
    ) -> None:
        self.provider = provider
        self.journal = journal
        self.reservation_store = reservation_store
        self.clock = clock
        self.lease_authority = lease_authority

    def prepare(
        self,
        *,
        transition: StateTransition,
        action: ActionIntent,
        authorization: AuthorizationBinding,
        fence: ExecutionFence,
        active_lease: ExecutionLease,
        context_binding: ContextBoundExecutionContext,
    ) -> PreparedDeploymentExecution:
        provenance = build_execution_context_provenance(
            context_binding.proposal
        )
        latest = self.journal.latest_for_action(
            resource_uid=transition.subject.resource_uid,
            action_hash=action.action_hash,
            authorization_hash=authorization.authorization_hash,
        )
        if (
            latest is not None
            and latest.state is ExecutionAttemptState.PREPARED
        ):
            stored_plan = latest.transition_plan
            if stored_plan is None:
                raise ProtocolViolation(
                    "open execution attempt has no durable transition plan"
                )
            plan = TransitionPlan.from_mapping(stored_plan)
        elif (
            latest is not None
            and latest.state is ExecutionAttemptState.COMMITTED
        ):
            raise ProtocolViolation(
                "execution action is already committed"
            )
        elif (
            latest is not None
            and latest.state is ExecutionAttemptState.UNKNOWN
        ):
            raise ProtocolViolation(
                "execution action has UNKNOWN prior attempt"
            )
        else:
            plan = self.provider.prepare_plan(
                transition=transition,
                action=action,
                observer=fence.lease_holder,
                now=self.clock(),
            )

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
            context_provenance=provenance,
        )

        reservation = None
        bound_context = context_binding
        proposal = context_binding.proposal
        if isinstance(proposal, TransitionProposalBindingV3):
            reservation = self.reservation_store.acquire(
                reservation_id=(
                    f"execution-attempt:{attempt.attempt_id}"
                ),
                work_id=proposal.work_id,
                expected_authority_generation=(
                    proposal.authority_generation
                ),
                expected_authority_hash=proposal.authority_hash,
                proposal_hash=proposal.proposal_hash,
                operation_id=attempt.operation_id,
                execution_lease=active_lease,
                now=self.clock(),
                lease_authority=self.lease_authority,
            )
            attempt = self.journal.bind_authority_reservation(
                attempt,
                reservation,
            )
            bound_context = replace(
                context_binding,
                authority_reservation=reservation,
                authority_reservation_store=self.reservation_store,
            )

        return PreparedDeploymentExecution(
            attempt=attempt,
            plan=plan,
            plan_authorization=plan_authorization,
            plan_fence=plan_fence,
            reservation=reservation,
            context_binding=bound_context,
        )

    def execute(
        self,
        *,
        transition: StateTransition,
        evidence: EvidenceBundle,
        outcome_contract: OutcomeContract,
        action: ActionIntent,
        authorization: AuthorizationBinding,
        fence: ExecutionFence,
        active_lease: ExecutionLease,
        caller: Principal,
        context_binding: ContextBoundExecutionContext,
    ) -> DurableDeploymentExecution:
        prepared = self.prepare(
            transition=transition,
            action=action,
            authorization=authorization,
            fence=fence,
            active_lease=active_lease,
            context_binding=context_binding,
        )
        attempt = prepared.attempt
        reservation = prepared.reservation

        validate_plan_execution(
            plan=prepared.plan,
            authorization=prepared.plan_authorization,
            fence=prepared.plan_fence,
            active_lease=active_lease,
            caller=caller,
            now=self.clock(),
        )
        if (
            attempt.plan_authorization_hash
            != prepared.plan_authorization.binding_hash
            or attempt.plan_fence_hash != prepared.plan_fence.fence_hash
        ):
            raise ProtocolViolation(
                "PREPARED attempt lost plan execution admission binding"
            )

        receipt = self.provider.execute_context_bound(
            transition=transition,
            evidence=evidence,
            outcome_contract=outcome_contract,
            action=action,
            authorization=authorization,
            fence=fence,
            active_lease=active_lease,
            caller=caller,
            now=self.clock(),
            operation_id=attempt.operation_id,
            execution_plan=prepared.plan,
            context_binding=prepared.context_binding,
        )
        if receipt.plan_hash != attempt.transition_plan_hash:
            raise ProtocolViolation(
                "provider receipt transition plan does not match PREPARED"
            )

        completed_at = self.clock()
        committed = self.journal.commit(
            attempt,
            completed_at=completed_at,
            result=_receipt_mapping(receipt),
        )
        if committed.authority_reservation is not None:
            self.reservation_store.release_after_terminal(
                committed.authority_reservation,
                now=completed_at,
            )

        return DurableDeploymentExecution(
            attempt=committed,
            receipt=receipt,
            reservation=reservation,
        )
