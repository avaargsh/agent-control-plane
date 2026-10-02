from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .provable_execution import ObservationSnapshot, TransitionPlan
from .state_transition_protocol import (
    ActionIntent,
    Principal,
    ProtocolViolation,
    ResourceIdentity,
    StateTransition,
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _required_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolViolation(f"kubernetes {field} is required")
    return value


def _required_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolViolation(f"kubernetes {field} must be an integer")
    return value


def capture_deployment_scale_observation(
    *,
    subject: ResourceIdentity,
    deployment: Mapping[str, Any],
    observer: Principal,
    observed_at: datetime,
) -> ObservationSnapshot:
    """Project live Kubernetes state into the provider-neutral observation."""

    if subject.provider != "kubernetes" or subject.kind != "Deployment":
        raise ProtocolViolation(
            "deployment scale observation requires kubernetes Deployment"
        )

    metadata = _mapping(deployment.get("metadata"))
    spec = _mapping(deployment.get("spec"))
    uid = _required_str(metadata.get("uid"), "metadata.uid")
    resource_version = _required_str(
        metadata.get("resourceVersion"),
        "metadata.resourceVersion",
    )
    generation = _required_int(
        metadata.get("generation"),
        "metadata.generation",
    )
    replicas = _required_int(spec.get("replicas"), "spec.replicas")

    if uid != subject.resource_uid:
        raise ProtocolViolation(
            "live kubernetes uid does not match observation subject"
        )
    if metadata.get("name") != subject.name:
        raise ProtocolViolation(
            "live kubernetes name does not match observation subject"
        )
    namespace = metadata.get("namespace", subject.namespace)
    if namespace != subject.namespace:
        raise ProtocolViolation(
            "live kubernetes namespace does not match observation subject"
        )

    return ObservationSnapshot.capture(
        subject=subject,
        observed_version=f"resourceVersion:{resource_version}",
        observed_at=observed_at,
        state={
            "generation": generation,
            "replicas": replicas,
        },
        observer=observer,
    )


def build_deployment_scale_plan(
    *,
    transition: StateTransition,
    action: ActionIntent,
    observation: ObservationSnapshot,
    created_at: datetime,
) -> TransitionPlan:
    """Derive the exact provider operation covered by legacy authorization.

    This is the compatibility bridge while AuthorizationBinding still binds the
    existing StateTransition + ActionIntent. The plan is deterministic over the
    observed provider precondition and those already-authorized objects.
    """

    transition.verify()
    action.verify()
    observation.verify()

    if observation.subject != transition.subject:
        raise ProtocolViolation(
            "scale plan observation subject does not match transition"
        )
    if action.transition_hash != transition.transition_hash:
        raise ProtocolViolation(
            "scale plan action is not bound to transition"
        )
    if action.provider != "kubernetes":
        raise ProtocolViolation(
            "scale plan requires kubernetes action provider"
        )
    if action.operation != "scale_deployment":
        raise ProtocolViolation(
            "scale plan requires scale_deployment action"
        )

    generation = observation.state.get("generation")
    replicas = observation.state.get("replicas")
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise ProtocolViolation(
            "scale plan observation generation is invalid"
        )
    if isinstance(replicas, bool) or not isinstance(replicas, int):
        raise ProtocolViolation(
            "scale plan observation replicas is invalid"
        )
    if generation != transition.expected_generation:
        raise ProtocolViolation(
            "scale plan observation generation is stale"
        )

    expected_before = transition.before.get("replicas")
    if expected_before is not None and replicas != expected_before:
        raise ProtocolViolation(
            "scale plan observation does not match transition before state"
        )

    desired_replicas = action.parameters.get("replicas")
    if transition.desired.get("replicas") != desired_replicas:
        raise ProtocolViolation(
            "scale plan action does not match transition desired state"
        )

    return TransitionPlan.seal(
        plan_id=f"{transition.transition_id}:provider-plan",
        observation=observation,
        before=transition.before,
        desired=transition.desired,
        provider=action.provider,
        operation=action.operation,
        parameters=action.parameters,
        preconditions={
            "observed_version": observation.observed_version,
            "generation": generation,
        },
        created_at=created_at,
    )


def validate_deployment_scale_plan(
    *,
    plan: TransitionPlan,
    observation: ObservationSnapshot,
    transition: StateTransition,
    action: ActionIntent,
) -> None:
    """Prove the generic plan is exactly covered by legacy protocol objects."""

    plan.verify(observation)
    transition.verify()
    action.verify()

    if plan.subject != transition.subject:
        raise ProtocolViolation("scale plan subject binding mismatch")
    if plan.before != transition.before:
        raise ProtocolViolation("scale plan before-state binding mismatch")
    if plan.desired != transition.desired:
        raise ProtocolViolation("scale plan desired-state binding mismatch")
    if plan.provider != action.provider:
        raise ProtocolViolation("scale plan provider binding mismatch")
    if plan.operation != action.operation:
        raise ProtocolViolation("scale plan operation binding mismatch")
    if plan.parameters != action.parameters:
        raise ProtocolViolation("scale plan parameter binding mismatch")
    if (
        plan.preconditions.get("observed_version")
        != observation.observed_version
    ):
        raise ProtocolViolation(
            "scale plan observed-version precondition mismatch"
        )
    if (
        plan.preconditions.get("generation")
        != transition.expected_generation
    ):
        raise ProtocolViolation(
            "scale plan generation precondition mismatch"
        )
