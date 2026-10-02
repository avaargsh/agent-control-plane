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
    SQLiteExecutionJournal,
)
from .kubernetes_deployment_transition import (
    ContextBoundExecutionContext,
    DeploymentScaleReceipt,
    KubernetesDeploymentScaleProvider,
)
from .state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    EvidenceBundle,
    ExecutionFence,
    ExecutionLease,
    OutcomeContract,
    Principal,
    StateTransition,
)


Clock = Callable[[], datetime]


@dataclass(frozen=True)
class PreparedDeploymentExecution:
    attempt: ExecutionAttempt
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
        attempt = self.journal.prepare(
            transition=transition,
            action=action,
            authorization=authorization,
            fence=fence,
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
            reservation=reservation,
            context_binding=prepared.context_binding,
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
            context_binding=bound_context,
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
