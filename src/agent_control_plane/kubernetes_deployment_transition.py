from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .authority_reservation import (
    AuthorityReservation,
    SQLiteAuthorityReservationStore,
)
from .context_transition import (
    ContextProposalBinding,
    TransitionProposalBindingV3,
    validate_context_binding,
    validate_context_bound_execution,
)
from .kubernetes_transition_plan import (
    build_deployment_scale_plan,
    capture_deployment_scale_observation,
    validate_deployment_scale_plan,
    validate_deployment_scale_plan_bindings,
    validate_deployment_scale_plan_precondition,
)
from .policy_replay import TransitionPolicyInput
from .provable_execution import (
    ObservationSnapshot,
    TransitionPlan,
    VerificationCondition,
    VerificationReport,
    VerificationStatus as ProofVerificationStatus,
)
from .runtime_clients import (
    RuntimeMutationOwnershipUncertain,
    RuntimeMutationUncertain,
)
from .transition_approval import (
    HMACApprovalVerifier,
    SignedTransitionApproval,
)
from .work_context import SQLiteWorkContextStore
from .execution_fencing import (
    ExecutionLeaseAuthority,
    _FENCE_EPOCH_ANNOTATION,
    _FENCE_HOLDER_ANNOTATION,
    _FENCE_LEASE_ID_ANNOTATION,
    assert_target_fence,
)
from .execution_verification import (
    ConditionEvaluation,
    OutcomeVerificationResult,
    VerificationStatus,
)
from .state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    EvidenceBundle,
    EvidenceItem,
    ExecutionFence,
    ExecutionLease,
    OutcomeCondition,
    OutcomeContract,
    Principal,
    ProtocolViolation,
    ResourceIdentity,
    StateTransition,
    validate_execution,
)


_ACTION_HASH_ANNOTATION = "agent-control-plane.openai.com/action-hash"
_TRANSITION_HASH_ANNOTATION = "agent-control-plane.openai.com/transition-hash"
_OPERATION_ID_ANNOTATION = "agent-control-plane.openai.com/operation-id"
_PLAN_HASH_ANNOTATION = "agent-control-plane.openai.com/plan-hash"
_AUTHORITY_RESERVATION_HASH_ANNOTATION = (
    "agent-control-plane.openai.com/authority-reservation-hash"
)


class KubernetesDeploymentApi(Protocol):
    def get_deployment(
        self,
        *,
        namespace: str,
        name: str,
    ) -> Mapping[str, Any] | None:
        ...

    def patch_deployment(
        self,
        *,
        namespace: str,
        name: str,
        patch: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def list_pods(
        self,
        *,
        namespace: str,
        selector: str,
    ) -> list[Mapping[str, Any]]:
        ...

    def list_events(
        self,
        *,
        namespace: str,
        involved_object_uid: str,
    ) -> list[Mapping[str, Any]]:
        ...


@dataclass(frozen=True)
class DeploymentScaleReceipt:
    resource_ref: str
    transition_hash: str
    action_hash: str
    operation_id: str
    desired_replicas: int
    changed: bool
    replayed: bool
    verified_after_uncertain_mutation: bool
    before_resource_version: str
    after_resource_version: str
    before_generation: int
    after_generation: int
    plan_hash: str | None = None
    authority_reservation_hash: str | None = None


@dataclass(frozen=True)
class ContextBoundExecutionContext:
    policy_input: TransitionPolicyInput
    proposal: ContextProposalBinding
    store: SQLiteWorkContextStore
    signed_approval: SignedTransitionApproval
    approval_verifier: HMACApprovalVerifier
    authority_reservation: AuthorityReservation | None = None
    authority_reservation_store: SQLiteAuthorityReservationStore | None = None


def _authority_reservation_for_execution(
    *,
    context_binding: ContextBoundExecutionContext,
    active_lease: ExecutionLease,
    operation_id: str,
    now: datetime,
    require_active: bool,
) -> AuthorityReservation | None:
    proposal = context_binding.proposal
    if not isinstance(proposal, TransitionProposalBindingV3):
        return None

    reservation = context_binding.authority_reservation
    reservation_store = context_binding.authority_reservation_store
    if reservation is None or reservation_store is None:
        raise ProtocolViolation(
            "proposal v3 execution requires pre-bound authority reservation"
        )

    reservation.verify()
    if reservation.work_id != proposal.work_id:
        raise ProtocolViolation(
            "authority reservation work does not match proposal v3"
        )
    if reservation.authority_generation != proposal.authority_generation:
        raise ProtocolViolation(
            "authority reservation generation does not match proposal v3"
        )
    if reservation.authority_hash != proposal.authority_hash:
        raise ProtocolViolation(
            "authority reservation hash does not match proposal v3"
        )
    if reservation.proposal_hash != proposal.proposal_hash:
        raise ProtocolViolation(
            "authority reservation proposal hash mismatch"
        )
    if reservation.operation_id != operation_id:
        raise ProtocolViolation(
            "authority reservation operation does not match execution"
        )
    reservation.assert_bound_lease(
        active_lease,
        now=now,
    )
    if require_active:
        reservation_store.assert_active(
            reservation,
            execution_lease=active_lease,
            now=now,
        )
    return reservation


@dataclass(frozen=True)
class DeploymentObservation:
    resource: ResourceIdentity
    observed_at: datetime
    snapshot: ObservationSnapshot
    deployment: Mapping[str, Any]
    pods: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]
    evidence_items: tuple[EvidenceItem, ...]
    evidence_bundle: EvidenceBundle


def _metadata(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    value = resource.get("metadata", {})
    return value if isinstance(value, Mapping) else {}


def _spec(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    value = resource.get("spec", {})
    return value if isinstance(value, Mapping) else {}


def _status(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    value = resource.get("status", {})
    return value if isinstance(value, Mapping) else {}


def _required_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolViolation(f"kubernetes {field} is required")
    return value


def _required_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolViolation(f"kubernetes {field} must be an integer")
    return value


def _annotations(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _metadata(resource).get("annotations", {})
    return value if isinstance(value, Mapping) else {}


def _selector(resource: Mapping[str, Any]) -> str:
    selector = _spec(resource).get("selector", {})
    if not isinstance(selector, Mapping):
        raise ProtocolViolation("deployment selector is required")
    labels = selector.get("matchLabels", {})
    if not isinstance(labels, Mapping) or not labels:
        raise ProtocolViolation(
            "deployment selector.matchLabels is required for observation"
        )
    parts = [
        f"{key}={labels[key]}"
        for key in sorted(labels)
        if isinstance(key, str)
        and isinstance(labels[key], (str, int, float, bool))
    ]
    if not parts:
        raise ProtocolViolation(
            "deployment selector.matchLabels has no scalar labels"
        )
    return ",".join(parts)


def _validate_subject(
    resource: Mapping[str, Any],
    subject: ResourceIdentity,
) -> tuple[Mapping[str, Any], Mapping[str, Any], int, str]:
    metadata = _metadata(resource)
    uid = _required_str(metadata.get("uid"), "metadata.uid")
    generation = _required_int(
        metadata.get("generation"),
        "metadata.generation",
    )
    resource_version = _required_str(
        metadata.get("resourceVersion"),
        "metadata.resourceVersion",
    )
    if uid != subject.resource_uid:
        raise ProtocolViolation(
            "live kubernetes uid does not match transition subject"
        )
    if metadata.get("name") != subject.name:
        raise ProtocolViolation(
            "live kubernetes name does not match transition subject"
        )
    namespace = metadata.get("namespace", subject.namespace)
    if namespace != subject.namespace:
        raise ProtocolViolation(
            "live kubernetes namespace does not match transition subject"
        )
    return metadata, _spec(resource), generation, resource_version


def _verify_replay_binding(
    *,
    transition: StateTransition,
    evidence: EvidenceBundle,
    outcome_contract: OutcomeContract,
    action: ActionIntent,
    authorization: AuthorizationBinding,
    fence: ExecutionFence,
    active_lease: ExecutionLease,
    caller: Principal,
    now: datetime,
) -> None:
    evidence.verify()
    outcome_contract.verify()
    transition.verify()
    action.verify()
    authorization.verify()
    active_lease.assert_active(now)

    if now >= authorization.expires_at or now >= fence.expires_at:
        raise ProtocolViolation("replay binding expired")
    if authorization.transition_hash != transition.transition_hash:
        raise ProtocolViolation("authorization transition binding mismatch")
    if authorization.evidence_hash != evidence.manifest_hash:
        raise ProtocolViolation("authorization evidence binding mismatch")
    if authorization.action_hash != action.action_hash:
        raise ProtocolViolation("authorization action binding mismatch")
    if fence.transition_hash != transition.transition_hash:
        raise ProtocolViolation("fence transition binding mismatch")
    if fence.action_hash != action.action_hash:
        raise ProtocolViolation("fence action binding mismatch")
    if fence.authorization_hash != authorization.authorization_hash:
        raise ProtocolViolation("fence authorization binding mismatch")
    if active_lease.lease_id != fence.lease_id:
        raise ProtocolViolation("stale execution lease id")
    if active_lease.epoch != fence.lease_epoch:
        raise ProtocolViolation("stale execution lease epoch")
    if active_lease.holder != fence.lease_holder or caller != fence.lease_holder:
        raise ProtocolViolation("caller does not hold execution lease")


class KubernetesDeploymentScaleProvider:
    """Execute one evidence-bound Deployment scale transition.

    The provider reads live Deployment identity immediately before mutation,
    validates the protocol against the live generation, and sends a
    resourceVersion-bound patch. The action/transition hashes are written in
    the same patch so a lost acknowledgement can be reconciled without
    claiming another actor's mutation.
    """

    def __init__(
        self,
        api: KubernetesDeploymentApi,
        *,
        lease_authority: ExecutionLeaseAuthority | None = None,
    ) -> None:
        self.api = api
        self.lease_authority = lease_authority

    def prepare_plan(
        self,
        *,
        transition: StateTransition,
        action: ActionIntent,
        observer: Principal,
        now: datetime,
    ) -> TransitionPlan:
        live = self.api.get_deployment(
            namespace=transition.subject.namespace,
            name=transition.subject.name,
        )
        if live is None:
            raise ProtocolViolation("deployment does not exist")
        observation = capture_deployment_scale_observation(
            subject=transition.subject,
            deployment=live,
            observer=observer,
            observed_at=now,
        )
        plan = build_deployment_scale_plan(
            transition=transition,
            action=action,
            observation=observation,
            created_at=now,
        )
        validate_deployment_scale_plan(
            plan=plan,
            observation=observation,
            transition=transition,
            action=action,
        )
        return plan

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
        now: datetime,
        operation_id: str | None = None,
        execution_plan: TransitionPlan | None = None,
    ) -> DeploymentScaleReceipt:
        return self._execute(
            transition=transition,
            evidence=evidence,
            outcome_contract=outcome_contract,
            action=action,
            authorization=authorization,
            fence=fence,
            active_lease=active_lease,
            caller=caller,
            now=now,
            operation_id=operation_id,
            execution_plan=execution_plan,
            context_binding=None,
        )

    def execute_context_bound(
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
        now: datetime,
        context_binding: ContextBoundExecutionContext,
        operation_id: str | None = None,
        execution_plan: TransitionPlan | None = None,
    ) -> DeploymentScaleReceipt:
        return self._execute(
            transition=transition,
            evidence=evidence,
            outcome_contract=outcome_contract,
            action=action,
            authorization=authorization,
            fence=fence,
            active_lease=active_lease,
            caller=caller,
            now=now,
            operation_id=operation_id,
            execution_plan=execution_plan,
            context_binding=context_binding,
        )

    def _execute(
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
        now: datetime,
        operation_id: str | None,
        execution_plan: TransitionPlan | None,
        context_binding: ContextBoundExecutionContext | None,
    ) -> DeploymentScaleReceipt:
        if transition.subject.provider != "kubernetes":
            raise ProtocolViolation("deployment provider requires kubernetes subject")
        if transition.subject.kind != "Deployment":
            raise ProtocolViolation("deployment provider requires Deployment subject")
        if action.provider != "kubernetes":
            raise ProtocolViolation("deployment action provider must be kubernetes")
        if action.operation != "scale_deployment":
            raise ProtocolViolation("unsupported kubernetes deployment operation")

        desired_replicas = action.parameters.get("replicas")
        if (
            isinstance(desired_replicas, bool)
            or not isinstance(desired_replicas, int)
            or desired_replicas < 0
        ):
            raise ProtocolViolation(
                "scale_deployment requires non-negative integer replicas"
            )
        if transition.desired.get("replicas") != desired_replicas:
            raise ProtocolViolation(
                "action replicas do not match transition desired state"
            )

        if execution_plan is not None:
            validate_deployment_scale_plan_bindings(
                plan=execution_plan,
                transition=transition,
                action=action,
            )

        live = self.api.get_deployment(
            namespace=transition.subject.namespace,
            name=transition.subject.name,
        )
        if live is None:
            raise ProtocolViolation("deployment does not exist")

        _, live_spec, generation, resource_version = _validate_subject(
            live,
            transition.subject,
        )
        if self.lease_authority is not None:
            self.lease_authority.assert_active(
                active_lease,
                now=now,
            )
            assert_target_fence(live, active_lease)
        live_replicas = live_spec.get("replicas")
        annotations = _annotations(live)

        if (
            live_replicas == desired_replicas
            and annotations.get(_ACTION_HASH_ANNOTATION) == action.action_hash
            and annotations.get(_TRANSITION_HASH_ANNOTATION)
            == transition.transition_hash
        ):
            if generation != transition.expected_generation + 1:
                raise ProtocolViolation(
                    "replay generation does not match owned scale mutation"
                )
            _verify_replay_binding(
                transition=transition,
                evidence=evidence,
                outcome_contract=outcome_contract,
                action=action,
                authorization=authorization,
                fence=fence,
                active_lease=active_lease,
                caller=caller,
                now=now,
            )
            if context_binding is not None:
                validate_context_binding(
                    transition=transition,
                    evidence=evidence,
                    action=action,
                    authorization=authorization,
                    now=now,
                    policy_input=context_binding.policy_input,
                    proposal=context_binding.proposal,
                    store=context_binding.store,
                    signed_approval=context_binding.signed_approval,
                    approval_verifier=(
                        context_binding.approval_verifier
                    ),
                )
            observed_operation_id = str(
                annotations.get(_OPERATION_ID_ANNOTATION, action.action_id)
            )
            if (
                execution_plan is not None
                and annotations.get(_PLAN_HASH_ANNOTATION)
                != execution_plan.plan_hash
            ):
                raise RuntimeMutationOwnershipUncertain(
                    "deployment replay lost transition plan ownership proof",
                    resource_ref=(
                        f"k8s://{transition.subject.namespace}/deployment/"
                        f"{transition.subject.name}"
                    ),
                    operation_id=(
                        operation_id
                        if operation_id is not None
                        else observed_operation_id
                    ),
                    observed_operation_id=observed_operation_id,
                )
            if (
                operation_id is not None
                and observed_operation_id != operation_id
            ):
                raise RuntimeMutationOwnershipUncertain(
                    "deployment already satisfies the action but belongs to "
                    "a different execution attempt",
                    resource_ref=(
                        f"k8s://{transition.subject.namespace}/deployment/"
                        f"{transition.subject.name}"
                    ),
                    operation_id=operation_id,
                    observed_operation_id=observed_operation_id,
                )
            operation_id = observed_operation_id
            reservation = None
            if context_binding is not None:
                reservation = _authority_reservation_for_execution(
                    context_binding=context_binding,
                    active_lease=active_lease,
                    operation_id=operation_id,
                    now=now,
                    require_active=False,
                )
                if (
                    reservation is not None
                    and annotations.get(
                        _AUTHORITY_RESERVATION_HASH_ANNOTATION
                    )
                    != reservation.reservation_hash
                ):
                    raise RuntimeMutationOwnershipUncertain(
                        "deployment replay lost authority reservation "
                        "ownership proof",
                        resource_ref=(
                            f"k8s://{transition.subject.namespace}/deployment/"
                            f"{transition.subject.name}"
                        ),
                        operation_id=operation_id,
                        observed_operation_id=observed_operation_id,
                    )
            return DeploymentScaleReceipt(
                resource_ref=(
                    f"k8s://{transition.subject.namespace}/deployment/"
                    f"{transition.subject.name}"
                ),
                transition_hash=transition.transition_hash,
                action_hash=action.action_hash,
                operation_id=operation_id,
                desired_replicas=desired_replicas,
                changed=False,
                replayed=True,
                verified_after_uncertain_mutation=False,
                before_resource_version=resource_version,
                after_resource_version=resource_version,
                before_generation=generation,
                after_generation=generation,
                plan_hash=(
                    str(annotations.get(_PLAN_HASH_ANNOTATION))
                    if annotations.get(_PLAN_HASH_ANNOTATION) is not None
                    else None
                ),
                authority_reservation_hash=(
                    reservation.reservation_hash
                    if reservation is not None
                    else (
                        str(
                            annotations.get(
                                _AUTHORITY_RESERVATION_HASH_ANNOTATION
                            )
                        )
                        if annotations.get(
                            _AUTHORITY_RESERVATION_HASH_ANNOTATION
                        )
                        is not None
                        else None
                    )
                ),
            )

        expected_before = transition.before.get("replicas")
        if expected_before is not None and live_replicas != expected_before:
            raise ProtocolViolation(
                "live replicas do not match transition before state"
            )

        if execution_plan is None:
            plan_observation = capture_deployment_scale_observation(
                subject=transition.subject,
                deployment=live,
                observer=caller,
                observed_at=now,
            )
            execution_plan = build_deployment_scale_plan(
                transition=transition,
                action=action,
                observation=plan_observation,
                created_at=now,
            )
            validate_deployment_scale_plan(
                plan=execution_plan,
                observation=plan_observation,
                transition=transition,
                action=action,
            )
        else:
            validate_deployment_scale_plan_precondition(
                plan=execution_plan,
                deployment=live,
            )

        operation_id = operation_id or uuid4().hex
        reservation = None
        if context_binding is None:
            validate_execution(
                transition=transition,
                evidence=evidence,
                outcome_contract=outcome_contract,
                action=action,
                authorization=authorization,
                fence=fence,
                active_lease=active_lease,
                current_generation=generation,
                caller=caller,
                now=now,
            )
        else:
            validate_context_bound_execution(
                transition=transition,
                evidence=evidence,
                outcome_contract=outcome_contract,
                action=action,
                authorization=authorization,
                fence=fence,
                active_lease=active_lease,
                current_generation=generation,
                caller=caller,
                now=now,
                policy_input=context_binding.policy_input,
                proposal=context_binding.proposal,
                store=context_binding.store,
                signed_approval=context_binding.signed_approval,
                approval_verifier=context_binding.approval_verifier,
            )
            reservation = _authority_reservation_for_execution(
                context_binding=context_binding,
                active_lease=active_lease,
                operation_id=operation_id,
                now=now,
                require_active=True,
            )
            if reservation is not None:
                # Revalidate while the reservation is ACTIVE. From this point
                # until terminal journal state, authoritative Work mutations
                # remain blocked by the shared Context DB.
                validate_context_bound_execution(
                    transition=transition,
                    evidence=evidence,
                    outcome_contract=outcome_contract,
                    action=action,
                    authorization=authorization,
                    fence=fence,
                    active_lease=active_lease,
                    current_generation=generation,
                    caller=caller,
                    now=now,
                    policy_input=context_binding.policy_input,
                    proposal=context_binding.proposal,
                    store=context_binding.store,
                    signed_approval=context_binding.signed_approval,
                    approval_verifier=context_binding.approval_verifier,
                )

        patch = {
            "metadata": {
                "resourceVersion": resource_version,
                "annotations": {
                    _ACTION_HASH_ANNOTATION: action.action_hash,
                    _TRANSITION_HASH_ANNOTATION: transition.transition_hash,
                    _OPERATION_ID_ANNOTATION: operation_id,
                    _PLAN_HASH_ANNOTATION: execution_plan.plan_hash,
                    **(
                        {
                            _AUTHORITY_RESERVATION_HASH_ANNOTATION: (
                                reservation.reservation_hash
                            ),
                        }
                        if reservation is not None
                        else {}
                    ),
                    **(
                        {
                            _FENCE_EPOCH_ANNOTATION: str(active_lease.epoch),
                            _FENCE_LEASE_ID_ANNOTATION: active_lease.lease_id,
                            _FENCE_HOLDER_ANNOTATION: (
                                f"{active_lease.holder.type}:"
                                f"{active_lease.holder.subject}"
                            ),
                        }
                        if self.lease_authority is not None
                        else {}
                    ),
                },
            },
            "spec": {
                "replicas": desired_replicas,
            },
        }

        verified_after_uncertain = False
        try:
            changed = self.api.patch_deployment(
                namespace=transition.subject.namespace,
                name=transition.subject.name,
                patch=patch,
            )
        except RuntimeMutationUncertain as exc:
            observed = self.api.get_deployment(
                namespace=transition.subject.namespace,
                name=transition.subject.name,
            )
            if observed is None:
                raise
            observed_annotations = _annotations(observed)
            observed_replicas = _spec(observed).get("replicas")
            if observed_replicas != desired_replicas:
                raise
            if (
                observed_annotations.get(_ACTION_HASH_ANNOTATION)
                != action.action_hash
                or observed_annotations.get(_TRANSITION_HASH_ANNOTATION)
                != transition.transition_hash
                or observed_annotations.get(_OPERATION_ID_ANNOTATION)
                != operation_id
                or observed_annotations.get(_PLAN_HASH_ANNOTATION)
                != execution_plan.plan_hash
                or (
                    reservation is not None
                    and observed_annotations.get(
                        _AUTHORITY_RESERVATION_HASH_ANNOTATION
                    )
                    != reservation.reservation_hash
                )
            ):
                raise RuntimeMutationOwnershipUncertain(
                    "deployment reached desired replicas after uncertain "
                    "mutation but this action does not own the postcondition",
                    resource_ref=(
                        f"k8s://{transition.subject.namespace}/deployment/"
                        f"{transition.subject.name}"
                    ),
                    operation_id=operation_id,
                    observed_operation_id=(
                        str(
                            observed_annotations.get(
                                _OPERATION_ID_ANNOTATION
                            )
                        )
                        if observed_annotations.get(
                            _OPERATION_ID_ANNOTATION
                        )
                        is not None
                        else None
                    ),
                ) from exc
            changed = observed
            verified_after_uncertain = True

        _, changed_spec, after_generation, after_resource_version = (
            _validate_subject(changed, transition.subject)
        )
        if changed_spec.get("replicas") != desired_replicas:
            raise ProtocolViolation(
                "kubernetes patch acknowledgement lacks desired replicas"
            )
        changed_annotations = _annotations(changed)
        if (
            changed_annotations.get(_ACTION_HASH_ANNOTATION)
            != action.action_hash
            or changed_annotations.get(_TRANSITION_HASH_ANNOTATION)
            != transition.transition_hash
            or changed_annotations.get(_OPERATION_ID_ANNOTATION)
            != operation_id
        ):
            raise ProtocolViolation(
                "kubernetes patch acknowledgement lost action ownership"
            )
        if reservation is not None:
            if (
                changed_annotations.get(
                    _AUTHORITY_RESERVATION_HASH_ANNOTATION
                )
                != reservation.reservation_hash
            ):
                raise ProtocolViolation(
                    "kubernetes patch acknowledgement lost authority reservation"
                )
            if context_binding is None:
                raise ProtocolViolation(
                    "authority reservation context is missing"
                )
            reservation_store = (
                context_binding.authority_reservation_store
            )
            if reservation_store is None:
                raise ProtocolViolation(
                    "authority reservation store is missing"
                )
            reservation_store.assert_active(
                reservation,
                execution_lease=active_lease,
                now=now,
            )

        return DeploymentScaleReceipt(
            resource_ref=(
                f"k8s://{transition.subject.namespace}/deployment/"
                f"{transition.subject.name}"
            ),
            transition_hash=transition.transition_hash,
            action_hash=action.action_hash,
            operation_id=operation_id,
            desired_replicas=desired_replicas,
            changed=True,
            replayed=False,
            verified_after_uncertain_mutation=verified_after_uncertain,
            before_resource_version=resource_version,
            after_resource_version=after_resource_version,
            before_generation=generation,
            after_generation=after_generation,
            plan_hash=execution_plan.plan_hash,
            authority_reservation_hash=(
                reservation.reservation_hash
                if reservation is not None
                else None
            ),
        )


class KubernetesDeploymentObserver:
    """Freshly observe Deployment + Pods + Events after execution."""

    def __init__(
        self,
        api: KubernetesDeploymentApi,
        *,
        collector_name: str = "kubernetes-deployment-observer",
        collector_version: str = "v1",
    ) -> None:
        self.api = api
        self.collector_name = collector_name
        self.collector_version = collector_version

    def observe(
        self,
        *,
        resource: ResourceIdentity,
        principal: Principal,
        observed_at: datetime,
    ) -> DeploymentObservation:
        deployment = self.api.get_deployment(
            namespace=resource.namespace,
            name=resource.name,
        )
        if deployment is None:
            raise ProtocolViolation("deployment missing during observation")
        _, _, generation, resource_version = _validate_subject(
            deployment,
            resource,
        )
        selector = _selector(deployment)
        pods = tuple(
            dict(item)
            for item in self.api.list_pods(
                namespace=resource.namespace,
                selector=selector,
            )
        )
        events = tuple(
            dict(item)
            for item in self.api.list_events(
                namespace=resource.namespace,
                involved_object_uid=resource.resource_uid,
            )
        )

        deployment_item = EvidenceItem.capture(
            evidence_id=(
                f"k8s-deployment:{resource.resource_uid}:{resource_version}"
            ),
            evidence_type="kubernetes_deployment",
            source=(
                f"kubernetes://{resource.namespace}/deployment/{resource.name}"
            ),
            collected_at=observed_at,
            collector_name=self.collector_name,
            collector_version=self.collector_version,
            principal=principal,
            query=f"get deployment/{resource.name}",
            payload=dict(deployment),
        )
        pods_item = EvidenceItem.capture(
            evidence_id=(
                f"k8s-pods:{resource.resource_uid}:{resource_version}"
            ),
            evidence_type="kubernetes_pods",
            source=f"kubernetes://{resource.namespace}/pods",
            collected_at=observed_at,
            collector_name=self.collector_name,
            collector_version=self.collector_version,
            principal=principal,
            query=f"list pods -l {selector}",
            payload={"items": list(pods)},
        )
        events_item = EvidenceItem.capture(
            evidence_id=(
                f"k8s-events:{resource.resource_uid}:{resource_version}"
            ),
            evidence_type="kubernetes_events",
            source=f"kubernetes://{resource.namespace}/events",
            collected_at=observed_at,
            collector_name=self.collector_name,
            collector_version=self.collector_version,
            principal=principal,
            query=f"list events involvedObject.uid={resource.resource_uid}",
            payload={"items": list(events)},
        )
        items = (deployment_item, pods_item, events_item)
        bundle = EvidenceBundle.seal(
            resource=resource,
            generation=generation,
            created_at=observed_at,
            items=items,
        )
        snapshot = capture_deployment_scale_observation(
            subject=resource,
            deployment=deployment,
            observer=principal,
            observed_at=observed_at,
        )
        return DeploymentObservation(
            resource=resource,
            observed_at=observed_at,
            snapshot=snapshot,
            deployment=dict(deployment),
            pods=pods,
            events=events,
            evidence_items=items,
            evidence_bundle=bundle,
        )


def build_deployment_execution_verification(
    *,
    plan: TransitionPlan,
    after_observation: ObservationSnapshot,
    expected_operation_id: str,
    expected_action_hash: str,
    expected_transition_hash: str,
    expected_authority_reservation_hash: str | None,
    verifier: Principal,
    verified_at: datetime,
) -> VerificationReport:
    """Build the independently checkable postcondition report for scale.

    The provider-neutral ObservationSnapshot retains only this control plane's
    ownership annotations. This lets the serialized execution proof carry the
    exact operation/plan/fence markers that justify
    OperationOwnershipProven=TRUE instead of relying on a provider ACK.
    """

    plan.verify()
    after_observation.verify()
    if after_observation.subject != plan.subject:
        raise ProtocolViolation(
            "deployment proof observation does not match transition plan"
        )

    desired_replicas = plan.desired.get("replicas")
    desired_reached = (
        isinstance(desired_replicas, int)
        and not isinstance(desired_replicas, bool)
        and after_observation.state.get("replicas") == desired_replicas
        and after_observation.state.get("readyReplicas") == desired_replicas
    )

    raw_ownership = after_observation.state.get("controlPlaneOwnership")
    ownership = (
        raw_ownership
        if isinstance(raw_ownership, Mapping)
        else {}
    )
    expected_ownership = {
        _ACTION_HASH_ANNOTATION: expected_action_hash,
        _TRANSITION_HASH_ANNOTATION: expected_transition_hash,
        _OPERATION_ID_ANNOTATION: expected_operation_id,
        _PLAN_HASH_ANNOTATION: plan.plan_hash,
    }
    if expected_authority_reservation_hash is not None:
        expected_ownership[_AUTHORITY_RESERVATION_HASH_ANNOTATION] = (
            expected_authority_reservation_hash
        )

    ownership_mismatches = tuple(
        key
        for key, expected in expected_ownership.items()
        if ownership.get(key) != expected
    )
    ownership_proven = not ownership_mismatches

    return VerificationReport.seal(
        plan=plan,
        after_observation=after_observation,
        conditions=(
            VerificationCondition(
                condition_type="DesiredStateReached",
                status=(
                    ProofVerificationStatus.TRUE
                    if desired_reached
                    else ProofVerificationStatus.FALSE
                ),
                reason=(
                    "DeploymentReady"
                    if desired_reached
                    else "DeploymentNotReady"
                ),
                message=(
                    f"desired replicas={desired_replicas}; "
                    f"observed replicas="
                    f"{after_observation.state.get('replicas')}; "
                    f"readyReplicas="
                    f"{after_observation.state.get('readyReplicas')}"
                ),
                evidence_digest=after_observation.observation_hash,
            ),
            VerificationCondition(
                condition_type="OperationOwnershipProven",
                status=(
                    ProofVerificationStatus.TRUE
                    if ownership_proven
                    else ProofVerificationStatus.FALSE
                ),
                reason=(
                    "ProviderOwnershipMarkersVerified"
                    if ownership_proven
                    else "ProviderOwnershipMarkersMismatch"
                ),
                message=(
                    "all expected Kubernetes execution ownership markers "
                    "match the fresh observation"
                    if ownership_proven
                    else "mismatched ownership markers: "
                    + ", ".join(ownership_mismatches)
                ),
                evidence_digest=after_observation.observation_hash,
            ),
        ),
        verifier=verifier,
        verified_at=verified_at,
    )


def _resolve_path(root: Mapping[str, Any], expression: str) -> Any:
    current: Any = root
    for part in expression.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(expression)
        current = current[part]
    return current


def _compare(observed: Any, condition: OutcomeCondition) -> bool | None:
    expected = condition.expected
    try:
        if condition.comparator == "eq":
            return observed == expected
        if condition.comparator == "ne":
            return observed != expected
        if condition.comparator == "lt":
            return observed < expected
        if condition.comparator == "lte":
            return observed <= expected
        if condition.comparator == "gt":
            return observed > expected
        if condition.comparator == "gte":
            return observed >= expected
    except TypeError:
        return None
    return None


def _evaluate_condition(
    condition: OutcomeCondition,
    *,
    observation: DeploymentObservation,
    safety_metrics: Mapping[str, Any],
) -> ConditionEvaluation:
    observed: Any
    try:
        if condition.source == "kubernetes":
            roots = {
                "deployment": observation.deployment,
                "pods": {"items": list(observation.pods)},
                "events": {"items": list(observation.events)},
            }
            observed = _resolve_path(roots, condition.expression)
        elif condition.source == "prometheus":
            if condition.expression not in safety_metrics:
                raise KeyError(condition.expression)
            observed = safety_metrics[condition.expression]
        else:
            raise KeyError(condition.source)
    except KeyError:
        return ConditionEvaluation(
            source=condition.source,
            expression=condition.expression,
            comparator=condition.comparator,
            expected=condition.expected,
            observed=None,
            passed=None,
        )
    return ConditionEvaluation(
        source=condition.source,
        expression=condition.expression,
        comparator=condition.comparator,
        expected=condition.expected,
        observed=observed,
        passed=_compare(observed, condition),
    )


def verify_outcome(
    *,
    transition: StateTransition,
    outcome_contract: OutcomeContract,
    observation: DeploymentObservation,
    safety_metrics: Mapping[str, Any] | None,
    checked_at: datetime,
) -> OutcomeVerificationResult:
    transition.verify()
    outcome_contract.verify()
    observation.evidence_bundle.verify(
        item_lookup={
            item.evidence_id: item
            for item in observation.evidence_items
        }
    )
    if transition.outcome_contract_hash != outcome_contract.contract_hash:
        raise ProtocolViolation("transition outcome contract mismatch")
    if observation.resource != transition.subject:
        raise ProtocolViolation("observation resource does not match transition")
    if outcome_contract.stabilization_seconds > 0:
        return OutcomeVerificationResult.seal(
            status=VerificationStatus.UNKNOWN,
            transition_hash=transition.transition_hash,
            outcome_contract_hash=outcome_contract.contract_hash,
            observation_hash=observation.evidence_bundle.manifest_hash,
            checked_at=checked_at,
            desired=(),
            safety=(),
            reasons=(
                "single-snapshot verifier cannot prove non-zero stabilization window",
            ),
        )

    metrics = safety_metrics or {}
    desired = tuple(
        _evaluate_condition(
            condition,
            observation=observation,
            safety_metrics=metrics,
        )
        for condition in outcome_contract.desired_conditions
    )
    safety = tuple(
        _evaluate_condition(
            condition,
            observation=observation,
            safety_metrics=metrics,
        )
        for condition in outcome_contract.safety_conditions
    )

    if any(item.passed is None for item in desired + safety):
        return OutcomeVerificationResult.seal(
            status=VerificationStatus.UNKNOWN,
            transition_hash=transition.transition_hash,
            outcome_contract_hash=outcome_contract.contract_hash,
            observation_hash=observation.evidence_bundle.manifest_hash,
            checked_at=checked_at,
            desired=desired,
            safety=safety,
            reasons=("required outcome evidence is missing or incomparable",),
        )

    failed_safety = tuple(item for item in safety if item.passed is False)
    if failed_safety:
        return OutcomeVerificationResult.seal(
            status=VerificationStatus.INVARIANT_VIOLATION,
            transition_hash=transition.transition_hash,
            outcome_contract_hash=outcome_contract.contract_hash,
            observation_hash=observation.evidence_bundle.manifest_hash,
            checked_at=checked_at,
            desired=desired,
            safety=safety,
            reasons=tuple(
                f"safety condition failed: {item.source}:{item.expression}"
                for item in failed_safety
            ),
        )

    if all(item.passed is True for item in desired):
        return OutcomeVerificationResult.seal(
            status=VerificationStatus.SUCCEEDED,
            transition_hash=transition.transition_hash,
            outcome_contract_hash=outcome_contract.contract_hash,
            observation_hash=observation.evidence_bundle.manifest_hash,
            checked_at=checked_at,
            desired=desired,
            safety=safety,
            reasons=("all desired and safety conditions passed",),
        )

    deadline = transition.created_at + timedelta(
        seconds=outcome_contract.deadline_seconds
    )
    if checked_at >= deadline:
        status = VerificationStatus.TIMED_OUT
        reasons = ("desired state was not reached before deadline",)
    else:
        status = VerificationStatus.DEGRADED
        reasons = ("desired state is not yet satisfied",)

    return OutcomeVerificationResult.seal(
        status=status,
        transition_hash=transition.transition_hash,
        outcome_contract_hash=outcome_contract.contract_hash,
        observation_hash=observation.evidence_bundle.manifest_hash,
        checked_at=checked_at,
        desired=desired,
        safety=safety,
        reasons=reasons,
    )
