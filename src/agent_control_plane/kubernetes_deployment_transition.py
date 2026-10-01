from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .runtime_clients import (
    RuntimeMutationOwnershipUncertain,
    RuntimeMutationUncertain,
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


@dataclass(frozen=True)
class DeploymentObservation:
    resource: ResourceIdentity
    observed_at: datetime
    deployment: Mapping[str, Any]
    pods: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]
    evidence_items: tuple[EvidenceItem, ...]
    evidence_bundle: EvidenceBundle


class VerificationStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    DEGRADED = "DEGRADED"
    TIMED_OUT = "TIMED_OUT"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ConditionEvaluation:
    source: str
    expression: str
    comparator: str
    expected: str | int | float | bool
    observed: Any
    passed: bool | None


@dataclass(frozen=True)
class OutcomeVerificationResult:
    status: VerificationStatus
    transition_hash: str
    outcome_contract_hash: str
    observation_hash: str
    checked_at: datetime
    desired: tuple[ConditionEvaluation, ...]
    safety: tuple[ConditionEvaluation, ...]
    reasons: tuple[str, ...]


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

    def __init__(self, api: KubernetesDeploymentApi) -> None:
        self.api = api

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
        live_replicas = live_spec.get("replicas")
        annotations = _annotations(live)

        if (
            live_replicas == desired_replicas
            and annotations.get(_ACTION_HASH_ANNOTATION) == action.action_hash
            and annotations.get(_TRANSITION_HASH_ANNOTATION)
            == transition.transition_hash
        ):
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
            operation_id = str(
                annotations.get(_OPERATION_ID_ANNOTATION, action.action_id)
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
            )

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

        expected_before = transition.before.get("replicas")
        if expected_before is not None and live_replicas != expected_before:
            raise ProtocolViolation(
                "live replicas do not match transition before state"
            )

        operation_id = uuid4().hex
        patch = {
            "metadata": {
                "resourceVersion": resource_version,
                "annotations": {
                    _ACTION_HASH_ANNOTATION: action.action_hash,
                    _TRANSITION_HASH_ANNOTATION: transition.transition_hash,
                    _OPERATION_ID_ANNOTATION: operation_id,
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
        return DeploymentObservation(
            resource=resource,
            observed_at=observed_at,
            deployment=dict(deployment),
            pods=pods,
            events=events,
            evidence_items=items,
            evidence_bundle=bundle,
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
        return OutcomeVerificationResult(
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
        return OutcomeVerificationResult(
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
        return OutcomeVerificationResult(
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
        return OutcomeVerificationResult(
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

    return OutcomeVerificationResult(
        status=status,
        transition_hash=transition.transition_hash,
        outcome_contract_hash=outcome_contract.contract_hash,
        observation_hash=observation.evidence_bundle.manifest_hash,
        checked_at=checked_at,
        desired=desired,
        safety=safety,
        reasons=reasons,
    )
