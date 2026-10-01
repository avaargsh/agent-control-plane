from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .context_overlay import ContextOverlay
from .execution_provenance import (
    ExecutionContextProvenance,
    ExecutionContextProvenanceV2,
)
from .policy_replay import (
    PolicyDecisionRecord,
    TransitionPolicyInput,
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
    canonical_digest,
    validate_execution,
)
from .transition_approval import (
    ApprovalDecision,
    HMACApprovalVerifier,
    SignedTransitionApproval,
    authorize_transition_from_approval,
)
from .work_context import (
    ContextProjection,
    SQLiteWorkContextStore,
    WorkStatus,
)


_CONTEXT_POLICY_KEY = "_agent_context_proposal"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class TransitionProposalBinding:
    """Bind one state-transition proposal to the exact work context seen.

    This object intentionally wraps, rather than modifies, StateTransition/v1.
    Existing transition hashes and replay semantics therefore remain stable.
    """

    proposal_id: str
    proposer: Principal
    transition_hash: str
    work_id: str
    work_version: int
    work_snapshot_hash: str
    projection_hash: str
    created_at: datetime
    proposal_hash: str
    proposal_version: str = "transition-proposal-binding/v1"

    @classmethod
    def seal(
        cls,
        *,
        proposal_id: str,
        proposer: Principal,
        transition: StateTransition,
        projection: ContextProjection,
        created_at: datetime,
    ) -> "TransitionProposalBinding":
        if not proposal_id:
            raise ProtocolViolation("proposal_id is required")
        _require_aware(created_at, "created_at")
        transition.verify()
        projection.verify()

        if projection.consumer != proposer:
            raise ProtocolViolation(
                "context projection consumer does not match proposer"
            )
        if projection.work.owner != proposer:
            raise ProtocolViolation(
                "transition proposer does not own projected work"
            )
        if projection.work.status is not WorkStatus.ACTIVE:
            raise ProtocolViolation(
                "transition proposal requires ACTIVE work"
            )

        provisional = cls(
            proposal_id=proposal_id,
            proposer=proposer,
            transition_hash=transition.transition_hash,
            work_id=projection.work.work_id,
            work_version=projection.work.version,
            work_snapshot_hash=projection.work.snapshot_hash,
            projection_hash=projection.projection_hash,
            created_at=created_at,
            proposal_hash="",
        )
        return cls(
            proposal_id=proposal_id,
            proposer=proposer,
            transition_hash=transition.transition_hash,
            work_id=projection.work.work_id,
            work_version=projection.work.version,
            work_snapshot_hash=projection.work.snapshot_hash,
            projection_hash=projection.projection_hash,
            created_at=created_at,
            proposal_hash=canonical_digest(
                provisional,
                exclude=("proposal_hash",),
            ),
        )

    def verify(self) -> None:
        if not self.proposal_id:
            raise ProtocolViolation("proposal_id is required")
        if self.work_version <= 0:
            raise ProtocolViolation("proposal work version must be positive")
        if not all(
            (
                self.transition_hash,
                self.work_id,
                self.work_snapshot_hash,
                self.projection_hash,
            )
        ):
            raise ProtocolViolation(
                "proposal context binding fields are required"
            )
        _require_aware(self.created_at, "created_at")
        actual = canonical_digest(
            self,
            exclude=("proposal_hash",),
        )
        if actual != self.proposal_hash:
            raise ProtocolViolation(
                "transition proposal binding digest mismatch"
            )

    def verify_projection(
        self,
        projection: ContextProjection,
    ) -> None:
        self.verify()
        projection.verify()
        expected = {
            "consumer": self.proposer,
            "work_id": self.work_id,
            "work_version": self.work_version,
            "work_snapshot_hash": self.work_snapshot_hash,
            "projection_hash": self.projection_hash,
        }
        actual = {
            "consumer": projection.consumer,
            "work_id": projection.work.work_id,
            "work_version": projection.work.version,
            "work_snapshot_hash": projection.work.snapshot_hash,
            "projection_hash": projection.projection_hash,
        }
        if actual != expected:
            raise ProtocolViolation(
                "context projection does not match transition proposal"
            )


@dataclass(frozen=True)
class TransitionProposalBindingV2:
    """Bind authority state and the exact non-authoritative overlay observed."""

    proposal_id: str
    proposer: Principal
    transition_hash: str
    work_id: str
    work_version: int
    work_snapshot_hash: str
    projection_hash: str
    context_revision: int
    context_head_hash: str
    context_overlay_hash: str
    created_at: datetime
    proposal_hash: str
    proposal_version: str = "transition-proposal-binding/v2"

    @classmethod
    def seal(
        cls,
        *,
        proposal_id: str,
        proposer: Principal,
        transition: StateTransition,
        projection: ContextProjection,
        context_overlay: ContextOverlay,
        created_at: datetime,
    ) -> "TransitionProposalBindingV2":
        if not proposal_id:
            raise ProtocolViolation("proposal_id is required")
        _require_aware(created_at, "created_at")
        transition.verify()
        projection.verify()
        context_overlay.verify()

        if projection.consumer != proposer:
            raise ProtocolViolation(
                "context projection consumer does not match proposer"
            )
        if projection.work.owner != proposer:
            raise ProtocolViolation(
                "transition proposer does not own projected work"
            )
        if projection.work.status is not WorkStatus.ACTIVE:
            raise ProtocolViolation(
                "transition proposal requires ACTIVE work"
            )
        if context_overlay.work_id != projection.work.work_id:
            raise ProtocolViolation(
                "context overlay belongs to another work item"
            )
        if context_overlay.authority_version != projection.work.version:
            raise ProtocolViolation(
                "context overlay authority version does not match projection"
            )
        if (
            context_overlay.authority_snapshot_hash
            != projection.work.snapshot_hash
        ):
            raise ProtocolViolation(
                "context overlay authority snapshot does not match projection"
            )

        provisional = cls(
            proposal_id=proposal_id,
            proposer=proposer,
            transition_hash=transition.transition_hash,
            work_id=projection.work.work_id,
            work_version=projection.work.version,
            work_snapshot_hash=projection.work.snapshot_hash,
            projection_hash=projection.projection_hash,
            context_revision=context_overlay.context_revision,
            context_head_hash=context_overlay.head_hash,
            context_overlay_hash=context_overlay.overlay_hash,
            created_at=created_at,
            proposal_hash="",
        )
        return cls(
            proposal_id=provisional.proposal_id,
            proposer=provisional.proposer,
            transition_hash=provisional.transition_hash,
            work_id=provisional.work_id,
            work_version=provisional.work_version,
            work_snapshot_hash=provisional.work_snapshot_hash,
            projection_hash=provisional.projection_hash,
            context_revision=provisional.context_revision,
            context_head_hash=provisional.context_head_hash,
            context_overlay_hash=provisional.context_overlay_hash,
            created_at=provisional.created_at,
            proposal_hash=canonical_digest(
                provisional,
                exclude=("proposal_hash",),
            ),
        )

    def verify(self) -> None:
        if not self.proposal_id:
            raise ProtocolViolation("proposal_id is required")
        if self.work_version <= 0:
            raise ProtocolViolation("proposal work version must be positive")
        if self.context_revision < 0:
            raise ProtocolViolation(
                "proposal context revision cannot be negative"
            )
        if not all(
            (
                self.transition_hash,
                self.work_id,
                self.work_snapshot_hash,
                self.projection_hash,
                self.context_head_hash,
                self.context_overlay_hash,
            )
        ):
            raise ProtocolViolation(
                "proposal v2 context binding fields are required"
            )
        _require_aware(self.created_at, "created_at")
        actual = canonical_digest(
            self,
            exclude=("proposal_hash",),
        )
        if actual != self.proposal_hash:
            raise ProtocolViolation(
                "transition proposal v2 binding digest mismatch"
            )

    def verify_projection(
        self,
        projection: ContextProjection,
    ) -> None:
        self.verify()
        projection.verify()
        expected = {
            "consumer": self.proposer,
            "work_id": self.work_id,
            "work_version": self.work_version,
            "work_snapshot_hash": self.work_snapshot_hash,
            "projection_hash": self.projection_hash,
        }
        actual = {
            "consumer": projection.consumer,
            "work_id": projection.work.work_id,
            "work_version": projection.work.version,
            "work_snapshot_hash": projection.work.snapshot_hash,
            "projection_hash": projection.projection_hash,
        }
        if actual != expected:
            raise ProtocolViolation(
                "context projection does not match transition proposal v2"
            )

    def verify_context_overlay(
        self,
        context_overlay: ContextOverlay,
    ) -> None:
        self.verify()
        context_overlay.verify()
        expected = {
            "work_id": self.work_id,
            "authority_version": self.work_version,
            "authority_snapshot_hash": self.work_snapshot_hash,
            "context_revision": self.context_revision,
            "head_hash": self.context_head_hash,
            "overlay_hash": self.context_overlay_hash,
        }
        actual = {
            "work_id": context_overlay.work_id,
            "authority_version": context_overlay.authority_version,
            "authority_snapshot_hash": (
                context_overlay.authority_snapshot_hash
            ),
            "context_revision": context_overlay.context_revision,
            "head_hash": context_overlay.head_hash,
            "overlay_hash": context_overlay.overlay_hash,
        }
        if actual != expected:
            raise ProtocolViolation(
                "context overlay does not match transition proposal v2"
            )


ContextProposalBinding = (
    TransitionProposalBinding | TransitionProposalBindingV2
)


def build_execution_context_provenance(
    proposal: ContextProposalBinding,
) -> ExecutionContextProvenance | ExecutionContextProvenanceV2:
    """Project a proposal binding into durable execution provenance."""

    proposal.verify()
    if isinstance(proposal, TransitionProposalBindingV2):
        return ExecutionContextProvenanceV2.seal(
            work_id=proposal.work_id,
            work_version=proposal.work_version,
            work_snapshot_hash=proposal.work_snapshot_hash,
            projection_hash=proposal.projection_hash,
            context_revision=proposal.context_revision,
            context_head_hash=proposal.context_head_hash,
            context_overlay_hash=proposal.context_overlay_hash,
            proposal_hash=proposal.proposal_hash,
            proposer=proposal.proposer,
        )
    return ExecutionContextProvenance.seal(
        work_id=proposal.work_id,
        work_version=proposal.work_version,
        work_snapshot_hash=proposal.work_snapshot_hash,
        projection_hash=proposal.projection_hash,
        proposal_hash=proposal.proposal_hash,
        proposer=proposal.proposer,
    )


def assert_proposal_fresh(
    *,
    proposal: ContextProposalBinding,
    store: SQLiteWorkContextStore,
) -> None:
    """Fail closed when canonical work moved after proposal construction."""

    proposal.verify()
    current = store.get(proposal.work_id)
    current.verify()

    if current.version != proposal.work_version:
        raise ProtocolViolation(
            "context proposal is stale: "
            f"projected work version {proposal.work_version}, "
            f"current {current.version}"
        )
    if current.snapshot_hash != proposal.work_snapshot_hash:
        raise ProtocolViolation(
            "context proposal work snapshot hash changed"
        )
    if current.owner != proposal.proposer:
        raise ProtocolViolation(
            "context proposal work ownership changed"
        )
    if current.status is not WorkStatus.ACTIVE:
        raise ProtocolViolation(
            "context proposal work is no longer ACTIVE"
        )


def _proposal_policy_value(
    proposal: ContextProposalBinding,
) -> dict[str, Any]:
    value = {
        "proposal_hash": proposal.proposal_hash,
        "proposal_version": proposal.proposal_version,
        "projection_hash": proposal.projection_hash,
        "work_id": proposal.work_id,
        "work_version": proposal.work_version,
        "work_snapshot_hash": proposal.work_snapshot_hash,
        "proposer": {
            "type": proposal.proposer.type,
            "subject": proposal.proposer.subject,
        },
    }
    if isinstance(proposal, TransitionProposalBindingV2):
        value.update(
            {
                "context_revision": proposal.context_revision,
                "context_head_hash": proposal.context_head_hash,
                "context_overlay_hash": proposal.context_overlay_hash,
            }
        )
    return value


def seal_context_bound_policy_input(
    *,
    policy_version: str,
    evidence: EvidenceBundle,
    transition: StateTransition,
    action: ActionIntent,
    principal: Principal,
    context: Mapping[str, Any],
    proposal: ContextProposalBinding,
    store: SQLiteWorkContextStore,
) -> TransitionPolicyInput:
    """Seal a policy input whose digest transitively binds proposal context."""

    proposal.verify()
    if proposal.transition_hash != transition.transition_hash:
        raise ProtocolViolation(
            "proposal is not bound to policy transition"
        )
    if proposal.proposer != principal:
        raise ProtocolViolation(
            "policy principal does not match transition proposer"
        )
    if _CONTEXT_POLICY_KEY in context:
        raise ProtocolViolation(
            f"policy context key {_CONTEXT_POLICY_KEY!r} is reserved"
        )

    assert_proposal_fresh(
        proposal=proposal,
        store=store,
    )

    bound_context = dict(context)
    bound_context[_CONTEXT_POLICY_KEY] = _proposal_policy_value(proposal)

    return TransitionPolicyInput.seal(
        policy_version=policy_version,
        evidence=evidence,
        transition=transition,
        action=action,
        principal=principal,
        context=bound_context,
    )


def verify_policy_binds_proposal(
    *,
    policy_input: TransitionPolicyInput,
    proposal: ContextProposalBinding,
) -> None:
    policy_input.verify()
    proposal.verify()

    if policy_input.principal != proposal.proposer:
        raise ProtocolViolation(
            "policy input principal does not match transition proposer"
        )

    raw = policy_input.context.get(_CONTEXT_POLICY_KEY)
    if not isinstance(raw, Mapping):
        raise ProtocolViolation(
            "policy input is missing transition proposal context binding"
        )

    expected = _proposal_policy_value(proposal)
    actual = dict(raw)
    if actual != expected:
        raise ProtocolViolation(
            "policy input transition proposal binding mismatch"
        )


def authorize_context_bound_transition(
    *,
    transition: StateTransition,
    action: ActionIntent,
    policy_input: TransitionPolicyInput,
    policy_decision: PolicyDecisionRecord,
    proposal: ContextProposalBinding,
    store: SQLiteWorkContextStore,
    signed_approval: SignedTransitionApproval,
    approval_verifier: HMACApprovalVerifier,
    authorization_expires_at: datetime,
    now: datetime,
) -> AuthorizationBinding:
    """Authorize only when the proposal context is still current."""

    proposal.verify()
    if proposal.transition_hash != transition.transition_hash:
        raise ProtocolViolation(
            "proposal transition binding mismatch"
        )
    verify_policy_binds_proposal(
        policy_input=policy_input,
        proposal=proposal,
    )
    assert_proposal_fresh(
        proposal=proposal,
        store=store,
    )

    return authorize_transition_from_approval(
        transition=transition,
        action=action,
        policy_input=policy_input,
        policy_decision=policy_decision,
        signed_approval=signed_approval,
        approval_verifier=approval_verifier,
        authorization_expires_at=authorization_expires_at,
        now=now,
    )


def validate_context_binding(
    *,
    transition: StateTransition,
    evidence: EvidenceBundle,
    action: ActionIntent,
    authorization: AuthorizationBinding,
    now: datetime,
    policy_input: TransitionPolicyInput,
    proposal: ContextProposalBinding,
    store: SQLiteWorkContextStore,
    signed_approval: SignedTransitionApproval,
    approval_verifier: HMACApprovalVerifier,
) -> None:
    """Verify signed proposal provenance and current work freshness."""

    proposal.verify()
    verify_policy_binds_proposal(
        policy_input=policy_input,
        proposal=proposal,
    )

    approval = approval_verifier.verify(
        signed_approval,
        now=now,
    )
    if approval.decision is not ApprovalDecision.APPROVE:
        raise ProtocolViolation(
            "execution transition approval decision is not APPROVE"
        )
    if authorization.approval_hash != approval.approval_hash:
        raise ProtocolViolation(
            "execution authorization approval binding mismatch"
        )
    if approval.policy_input_hash != policy_input.input_hash:
        raise ProtocolViolation(
            "execution approval policy input binding mismatch"
        )
    if approval.policy_decision_hash != authorization.policy_decision_hash:
        raise ProtocolViolation(
            "execution approval policy decision binding mismatch"
        )
    if approval.execution_principal != authorization.principal:
        raise ProtocolViolation(
            "execution approval principal binding mismatch"
        )
    if approval.transition_hash != transition.transition_hash:
        raise ProtocolViolation(
            "execution approval transition binding mismatch"
        )
    if approval.evidence_hash != evidence.manifest_hash:
        raise ProtocolViolation(
            "execution approval evidence binding mismatch"
        )
    if approval.action_hash != action.action_hash:
        raise ProtocolViolation(
            "execution approval action binding mismatch"
        )
    if approval.generation != transition.expected_generation:
        raise ProtocolViolation(
            "execution approval generation binding mismatch"
        )
    if proposal.transition_hash != transition.transition_hash:
        raise ProtocolViolation(
            "execution proposal transition binding mismatch"
        )

    assert_proposal_fresh(
        proposal=proposal,
        store=store,
    )


def validate_context_bound_execution(
    *,
    transition: StateTransition,
    evidence: EvidenceBundle,
    outcome_contract: OutcomeContract,
    action: ActionIntent,
    authorization: AuthorizationBinding,
    fence: ExecutionFence,
    active_lease: ExecutionLease,
    current_generation: int,
    caller: Principal,
    now: datetime,
    policy_input: TransitionPolicyInput,
    proposal: ContextProposalBinding,
    store: SQLiteWorkContextStore,
    signed_approval: SignedTransitionApproval,
    approval_verifier: HMACApprovalVerifier,
) -> None:
    """Re-check context provenance, freshness and execution fencing."""

    validate_context_binding(
        transition=transition,
        evidence=evidence,
        action=action,
        authorization=authorization,
        now=now,
        policy_input=policy_input,
        proposal=proposal,
        store=store,
        signed_approval=signed_approval,
        approval_verifier=approval_verifier,
    )

    validate_execution(
        transition=transition,
        evidence=evidence,
        outcome_contract=outcome_contract,
        action=action,
        authorization=authorization,
        fence=fence,
        active_lease=active_lease,
        current_generation=current_generation,
        caller=caller,
        now=now,
    )
