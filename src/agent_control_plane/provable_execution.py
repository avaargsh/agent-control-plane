from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol

from .state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    ExecutionLease,
    Principal,
    ProtocolViolation,
    ResourceIdentity,
    StateTransition,
    canonical_digest,
)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


def _snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolViolation(
            "provider-neutral payload must be JSON-serializable"
        ) from exc
    return json.loads(encoded)


def _resource_mapping(value: ResourceIdentity) -> dict[str, str]:
    return {
        "provider": value.provider,
        "resource_uid": value.resource_uid,
        "namespace": value.namespace,
        "kind": value.kind,
        "name": value.name,
    }


def _resource_from_mapping(value: Mapping[str, Any]) -> ResourceIdentity:
    try:
        return ResourceIdentity(
            provider=str(value["provider"]),
            resource_uid=str(value["resource_uid"]),
            namespace=str(value["namespace"]),
            kind=str(value["kind"]),
            name=str(value["name"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolViolation("invalid resource identity mapping") from exc


def _principal_mapping(value: Principal) -> dict[str, str]:
    return {
        "type": value.type,
        "subject": value.subject,
    }


def _principal_from_mapping(value: Mapping[str, Any]) -> Principal:
    try:
        return Principal(
            type=str(value["type"]),
            subject=str(value["subject"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolViolation("invalid principal mapping") from exc


@dataclass(frozen=True)
class ObservationSnapshot:
    """Immutable observation of external provider reality."""

    subject: ResourceIdentity
    observed_version: str
    observed_at: datetime
    state: Mapping[str, Any]
    observer: Principal
    observation_hash: str
    observation_version: str = "observation-snapshot/v1"

    @classmethod
    def capture(
        cls,
        *,
        subject: ResourceIdentity,
        observed_version: str,
        observed_at: datetime,
        state: Mapping[str, Any],
        observer: Principal,
    ) -> "ObservationSnapshot":
        if not observed_version:
            raise ProtocolViolation("observed_version is required")
        _require_aware(observed_at, "observed_at")
        state_snapshot = _snapshot(state)
        provisional = cls(
            subject=subject,
            observed_version=observed_version,
            observed_at=observed_at,
            state=state_snapshot,
            observer=observer,
            observation_hash="",
        )
        return cls(
            subject=subject,
            observed_version=observed_version,
            observed_at=observed_at,
            state=state_snapshot,
            observer=observer,
            observation_hash=canonical_digest(
                provisional,
                exclude=("observation_hash",),
            ),
        )

    def verify(self) -> None:
        if not self.observed_version:
            raise ProtocolViolation("observed_version is required")
        _require_aware(self.observed_at, "observed_at")
        actual = canonical_digest(
            self,
            exclude=("observation_hash",),
        )
        if actual != self.observation_hash:
            raise ProtocolViolation(
                "observation digest mismatch: "
                f"expected {self.observation_hash}, got {actual}"
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "ObservationSnapshot":
        subject = value.get("subject")
        observer = value.get("observer")
        if not isinstance(subject, Mapping) or not isinstance(
            observer,
            Mapping,
        ):
            raise ProtocolViolation(
                "observation subject/observer mappings are required"
            )
        try:
            snapshot = cls(
                subject=_resource_from_mapping(subject),
                observed_version=str(value["observed_version"]),
                observed_at=datetime.fromisoformat(
                    str(value["observed_at"])
                ),
                state=_snapshot(value["state"]),
                observer=_principal_from_mapping(observer),
                observation_hash=str(value["observation_hash"]),
                observation_version=str(
                    value.get(
                        "observation_version",
                        "observation-snapshot/v1",
                    )
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolViolation(
                "invalid observation snapshot mapping"
            ) from exc
        snapshot.verify()
        return snapshot

    def as_mapping(self) -> dict[str, Any]:
        self.verify()
        return {
            "subject": _resource_mapping(self.subject),
            "observed_version": self.observed_version,
            "observed_at": self.observed_at.isoformat(),
            "state": dict(self.state),
            "observer": _principal_mapping(self.observer),
            "observation_hash": self.observation_hash,
            "observation_version": self.observation_version,
        }


@dataclass(frozen=True)
class TransitionPlan:
    """Exact provider operation approved against one immutable observation."""

    plan_id: str
    subject: ResourceIdentity
    observation_hash: str
    before: Mapping[str, Any]
    desired: Mapping[str, Any]
    provider: str
    operation: str
    parameters: Mapping[str, Any]
    preconditions: Mapping[str, Any]
    created_at: datetime
    before_hash: str
    desired_hash: str
    plan_hash: str
    plan_version: str = "transition-plan/v1"

    @classmethod
    def seal(
        cls,
        *,
        plan_id: str,
        observation: ObservationSnapshot,
        before: Mapping[str, Any],
        desired: Mapping[str, Any],
        provider: str,
        operation: str,
        parameters: Mapping[str, Any],
        preconditions: Mapping[str, Any],
        created_at: datetime,
    ) -> "TransitionPlan":
        if not all((plan_id, provider, operation)):
            raise ProtocolViolation(
                "plan_id, provider, and operation are required"
            )
        _require_aware(created_at, "created_at")
        observation.verify()

        before_snapshot = _snapshot(before)
        desired_snapshot = _snapshot(desired)
        parameter_snapshot = _snapshot(parameters)
        precondition_snapshot = _snapshot(preconditions)

        if before_snapshot == desired_snapshot:
            raise ProtocolViolation("transition plan cannot be a no-op")

        before_hash = canonical_digest(before_snapshot)
        desired_hash = canonical_digest(desired_snapshot)
        provisional = cls(
            plan_id=plan_id,
            subject=observation.subject,
            observation_hash=observation.observation_hash,
            before=before_snapshot,
            desired=desired_snapshot,
            provider=provider,
            operation=operation,
            parameters=parameter_snapshot,
            preconditions=precondition_snapshot,
            created_at=created_at,
            before_hash=before_hash,
            desired_hash=desired_hash,
            plan_hash="",
        )
        return cls(
            plan_id=provisional.plan_id,
            subject=provisional.subject,
            observation_hash=provisional.observation_hash,
            before=provisional.before,
            desired=provisional.desired,
            provider=provisional.provider,
            operation=provisional.operation,
            parameters=provisional.parameters,
            preconditions=provisional.preconditions,
            created_at=provisional.created_at,
            before_hash=provisional.before_hash,
            desired_hash=provisional.desired_hash,
            plan_hash=canonical_digest(
                provisional,
                exclude=("plan_hash",),
            ),
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "TransitionPlan":
        subject_value = value.get("subject")
        if not isinstance(subject_value, Mapping):
            raise ProtocolViolation(
                "transition plan subject mapping is required"
            )
        try:
            created_at = datetime.fromisoformat(
                str(value["created_at"])
            )
            plan = cls(
                plan_id=str(value["plan_id"]),
                subject=ResourceIdentity(
                    provider=str(subject_value["provider"]),
                    resource_uid=str(subject_value["resource_uid"]),
                    namespace=str(subject_value["namespace"]),
                    kind=str(subject_value["kind"]),
                    name=str(subject_value["name"]),
                ),
                observation_hash=str(value["observation_hash"]),
                before=_snapshot(value["before"]),
                desired=_snapshot(value["desired"]),
                provider=str(value["provider"]),
                operation=str(value["operation"]),
                parameters=_snapshot(value["parameters"]),
                preconditions=_snapshot(value["preconditions"]),
                created_at=created_at,
                before_hash=str(value["before_hash"]),
                desired_hash=str(value["desired_hash"]),
                plan_hash=str(value["plan_hash"]),
                plan_version=str(
                    value.get(
                        "plan_version",
                        "transition-plan/v1",
                    )
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolViolation(
                "invalid transition plan mapping"
            ) from exc
        plan.verify()
        return plan

    def verify(
        self,
        observation: ObservationSnapshot | None = None,
    ) -> None:
        _require_aware(self.created_at, "created_at")
        if not all((self.plan_id, self.provider, self.operation)):
            raise ProtocolViolation(
                "plan_id, provider, and operation are required"
            )
        if canonical_digest(self.before) != self.before_hash:
            raise ProtocolViolation("transition plan before digest mismatch")
        if canonical_digest(self.desired) != self.desired_hash:
            raise ProtocolViolation("transition plan desired digest mismatch")
        if self.before == self.desired:
            raise ProtocolViolation("transition plan cannot be a no-op")

        actual = canonical_digest(
            self,
            exclude=("plan_hash",),
        )
        if actual != self.plan_hash:
            raise ProtocolViolation(
                "transition plan digest mismatch: "
                f"expected {self.plan_hash}, got {actual}"
            )

        if observation is None:
            return
        observation.verify()
        if observation.subject != self.subject:
            raise ProtocolViolation(
                "transition plan observation subject mismatch"
            )
        if observation.observation_hash != self.observation_hash:
            raise ProtocolViolation(
                "transition plan observation binding mismatch"
            )

    def as_mapping(self) -> dict[str, Any]:
        self.verify()
        return {
            "plan_id": self.plan_id,
            "subject": _resource_mapping(self.subject),
            "observation_hash": self.observation_hash,
            "before": dict(self.before),
            "desired": dict(self.desired),
            "provider": self.provider,
            "operation": self.operation,
            "parameters": dict(self.parameters),
            "preconditions": dict(self.preconditions),
            "created_at": self.created_at.isoformat(),
            "before_hash": self.before_hash,
            "desired_hash": self.desired_hash,
            "plan_hash": self.plan_hash,
            "plan_version": self.plan_version,
        }


@dataclass(frozen=True)
class PlanAuthorizationBinding:
    """Bind existing authority to one exact provider TransitionPlan."""

    plan_hash: str
    transition_hash: str
    action_hash: str
    source_authorization_hash: str
    principal: Principal
    expires_at: datetime
    binding_hash: str
    binding_version: str = "plan-authorization-binding/v1"

    @classmethod
    def derive(
        cls,
        *,
        plan: TransitionPlan,
        transition: StateTransition,
        action: ActionIntent,
        authorization: AuthorizationBinding,
    ) -> "PlanAuthorizationBinding":
        plan.verify()
        transition.verify()
        action.verify()
        authorization.verify()

        if plan.subject != transition.subject:
            raise ProtocolViolation(
                "plan authorization subject mismatch"
            )
        if plan.before != transition.before:
            raise ProtocolViolation(
                "plan authorization before-state mismatch"
            )
        if plan.desired != transition.desired:
            raise ProtocolViolation(
                "plan authorization desired-state mismatch"
            )
        if plan.provider != action.provider:
            raise ProtocolViolation(
                "plan authorization provider mismatch"
            )
        if plan.operation != action.operation:
            raise ProtocolViolation(
                "plan authorization operation mismatch"
            )
        if plan.parameters != action.parameters:
            raise ProtocolViolation(
                "plan authorization parameters mismatch"
            )
        if action.transition_hash != transition.transition_hash:
            raise ProtocolViolation(
                "plan authorization action transition mismatch"
            )
        if authorization.transition_hash != transition.transition_hash:
            raise ProtocolViolation(
                "plan authorization source transition mismatch"
            )
        if authorization.action_hash != action.action_hash:
            raise ProtocolViolation(
                "plan authorization source action mismatch"
            )
        if authorization.evidence_hash != transition.evidence_hash:
            raise ProtocolViolation(
                "plan authorization source evidence mismatch"
            )

        provisional = cls(
            plan_hash=plan.plan_hash,
            transition_hash=transition.transition_hash,
            action_hash=action.action_hash,
            source_authorization_hash=authorization.authorization_hash,
            principal=authorization.principal,
            expires_at=authorization.expires_at,
            binding_hash="",
        )
        return cls(
            plan_hash=provisional.plan_hash,
            transition_hash=provisional.transition_hash,
            action_hash=provisional.action_hash,
            source_authorization_hash=(
                provisional.source_authorization_hash
            ),
            principal=provisional.principal,
            expires_at=provisional.expires_at,
            binding_hash=canonical_digest(
                provisional,
                exclude=("binding_hash",),
            ),
        )

    def verify(self) -> None:
        _require_aware(self.expires_at, "expires_at")
        if not all(
            (
                self.plan_hash,
                self.transition_hash,
                self.action_hash,
                self.source_authorization_hash,
            )
        ):
            raise ProtocolViolation(
                "plan authorization digest bindings are required"
            )
        actual = canonical_digest(
            self,
            exclude=("binding_hash",),
        )
        if actual != self.binding_hash:
            raise ProtocolViolation(
                "plan authorization digest mismatch: "
                f"expected {self.binding_hash}, got {actual}"
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "PlanAuthorizationBinding":
        principal = value.get("principal")
        if not isinstance(principal, Mapping):
            raise ProtocolViolation(
                "plan authorization principal mapping is required"
            )
        try:
            binding = cls(
                plan_hash=str(value["plan_hash"]),
                transition_hash=str(value["transition_hash"]),
                action_hash=str(value["action_hash"]),
                source_authorization_hash=str(
                    value["source_authorization_hash"]
                ),
                principal=_principal_from_mapping(principal),
                expires_at=datetime.fromisoformat(
                    str(value["expires_at"])
                ),
                binding_hash=str(value["binding_hash"]),
                binding_version=str(
                    value.get(
                        "binding_version",
                        "plan-authorization-binding/v1",
                    )
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolViolation(
                "invalid plan authorization mapping"
            ) from exc
        binding.verify()
        return binding

    def as_mapping(self) -> dict[str, Any]:
        self.verify()
        return {
            "plan_hash": self.plan_hash,
            "transition_hash": self.transition_hash,
            "action_hash": self.action_hash,
            "source_authorization_hash": self.source_authorization_hash,
            "principal": _principal_mapping(self.principal),
            "expires_at": self.expires_at.isoformat(),
            "binding_hash": self.binding_hash,
            "binding_version": self.binding_version,
        }


@dataclass(frozen=True)
class PlanExecutionFence:
    """Provider-neutral execution fence bound to plan + authority + lease."""

    resource_uid: str
    plan_hash: str
    plan_authorization_hash: str
    lease_id: str
    lease_holder: Principal
    lease_epoch: int
    expires_at: datetime
    fence_hash: str
    fence_version: str = "plan-execution-fence/v1"

    @classmethod
    def bind(
        cls,
        *,
        plan: TransitionPlan,
        authorization: PlanAuthorizationBinding,
        lease: ExecutionLease,
    ) -> "PlanExecutionFence":
        plan.verify()
        authorization.verify()
        if plan.subject.resource_uid != lease.resource_uid:
            raise ProtocolViolation(
                "plan fence lease resource mismatch"
            )
        if authorization.plan_hash != plan.plan_hash:
            raise ProtocolViolation(
                "plan fence authorization plan mismatch"
            )
        provisional = cls(
            resource_uid=plan.subject.resource_uid,
            plan_hash=plan.plan_hash,
            plan_authorization_hash=authorization.binding_hash,
            lease_id=lease.lease_id,
            lease_holder=lease.holder,
            lease_epoch=lease.epoch,
            expires_at=min(
                authorization.expires_at,
                lease.expires_at,
            ),
            fence_hash="",
        )
        return cls(
            resource_uid=provisional.resource_uid,
            plan_hash=provisional.plan_hash,
            plan_authorization_hash=(
                provisional.plan_authorization_hash
            ),
            lease_id=provisional.lease_id,
            lease_holder=provisional.lease_holder,
            lease_epoch=provisional.lease_epoch,
            expires_at=provisional.expires_at,
            fence_hash=canonical_digest(
                provisional,
                exclude=("fence_hash",),
            ),
        )

    def verify(self) -> None:
        _require_aware(self.expires_at, "expires_at")
        if self.lease_epoch <= 0:
            raise ProtocolViolation(
                "plan execution fence lease epoch must be positive"
            )
        actual = canonical_digest(
            self,
            exclude=("fence_hash",),
        )
        if actual != self.fence_hash:
            raise ProtocolViolation(
                "plan execution fence digest mismatch: "
                f"expected {self.fence_hash}, got {actual}"
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "PlanExecutionFence":
        holder = value.get("lease_holder")
        if not isinstance(holder, Mapping):
            raise ProtocolViolation(
                "plan execution fence lease holder mapping is required"
            )
        try:
            fence = cls(
                resource_uid=str(value["resource_uid"]),
                plan_hash=str(value["plan_hash"]),
                plan_authorization_hash=str(
                    value["plan_authorization_hash"]
                ),
                lease_id=str(value["lease_id"]),
                lease_holder=_principal_from_mapping(holder),
                lease_epoch=int(value["lease_epoch"]),
                expires_at=datetime.fromisoformat(
                    str(value["expires_at"])
                ),
                fence_hash=str(value["fence_hash"]),
                fence_version=str(
                    value.get(
                        "fence_version",
                        "plan-execution-fence/v1",
                    )
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolViolation(
                "invalid plan execution fence mapping"
            ) from exc
        fence.verify()
        return fence

    def as_mapping(self) -> dict[str, Any]:
        self.verify()
        return {
            "resource_uid": self.resource_uid,
            "plan_hash": self.plan_hash,
            "plan_authorization_hash": self.plan_authorization_hash,
            "lease_id": self.lease_id,
            "lease_holder": _principal_mapping(self.lease_holder),
            "lease_epoch": self.lease_epoch,
            "expires_at": self.expires_at.isoformat(),
            "fence_hash": self.fence_hash,
            "fence_version": self.fence_version,
        }


def validate_plan_execution(
    *,
    plan: TransitionPlan,
    authorization: PlanAuthorizationBinding,
    fence: PlanExecutionFence,
    active_lease: ExecutionLease,
    caller: Principal,
    now: datetime,
) -> None:
    """Fail closed before a provider side effect without provider semantics."""

    plan.verify()
    authorization.verify()
    fence.verify()
    active_lease.assert_active(now)
    _require_aware(now, "now")

    if now >= authorization.expires_at or now >= fence.expires_at:
        raise ProtocolViolation("plan execution admission expired")
    if authorization.plan_hash != plan.plan_hash:
        raise ProtocolViolation(
            "plan execution authorization mismatch"
        )
    if fence.plan_hash != plan.plan_hash:
        raise ProtocolViolation(
            "plan execution fence plan mismatch"
        )
    if (
        fence.plan_authorization_hash
        != authorization.binding_hash
    ):
        raise ProtocolViolation(
            "plan execution fence authorization mismatch"
        )
    if fence.resource_uid != plan.subject.resource_uid:
        raise ProtocolViolation(
            "plan execution fence resource mismatch"
        )
    if active_lease.resource_uid != plan.subject.resource_uid:
        raise ProtocolViolation(
            "active lease resource does not match plan"
        )
    if active_lease.lease_id != fence.lease_id:
        raise ProtocolViolation("stale plan execution lease id")
    if active_lease.epoch != fence.lease_epoch:
        raise ProtocolViolation("stale plan execution lease epoch")
    if active_lease.holder != fence.lease_holder:
        raise ProtocolViolation(
            "plan execution lease holder mismatch"
        )
    if caller != fence.lease_holder:
        raise ProtocolViolation(
            "caller does not hold plan execution lease"
        )


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True)
class PolicyDecisionEnvelope:
    """Verifiable reference to an external policy decision."""

    engine_identity: str
    policy_set_digest: str
    policy_input_digest: str
    decision: PolicyDecision
    determining_policy_refs: tuple[str, ...]
    evaluated_at: datetime
    decision_hash: str
    envelope_version: str = "policy-decision-envelope/v1"

    @classmethod
    def seal(
        cls,
        *,
        engine_identity: str,
        policy_set_digest: str,
        policy_input_digest: str,
        decision: PolicyDecision,
        determining_policy_refs: tuple[str, ...],
        evaluated_at: datetime,
    ) -> "PolicyDecisionEnvelope":
        if not all(
            (
                engine_identity,
                policy_set_digest,
                policy_input_digest,
            )
        ):
            raise ProtocolViolation(
                "policy engine and digest bindings are required"
            )
        _require_aware(evaluated_at, "evaluated_at")
        canonical_refs = tuple(sorted(set(determining_policy_refs)))
        provisional = cls(
            engine_identity=engine_identity,
            policy_set_digest=policy_set_digest,
            policy_input_digest=policy_input_digest,
            decision=decision,
            determining_policy_refs=canonical_refs,
            evaluated_at=evaluated_at,
            decision_hash="",
        )
        return cls(
            engine_identity=provisional.engine_identity,
            policy_set_digest=provisional.policy_set_digest,
            policy_input_digest=provisional.policy_input_digest,
            decision=provisional.decision,
            determining_policy_refs=provisional.determining_policy_refs,
            evaluated_at=provisional.evaluated_at,
            decision_hash=canonical_digest(
                provisional,
                exclude=("decision_hash",),
            ),
        )

    def verify(self) -> None:
        _require_aware(self.evaluated_at, "evaluated_at")
        if tuple(sorted(set(self.determining_policy_refs))) != (
            self.determining_policy_refs
        ):
            raise ProtocolViolation(
                "determining policy refs must be unique and sorted"
            )
        actual = canonical_digest(
            self,
            exclude=("decision_hash",),
        )
        if actual != self.decision_hash:
            raise ProtocolViolation(
                "policy decision digest mismatch: "
                f"expected {self.decision_hash}, got {actual}"
            )


@dataclass(frozen=True)
class ProviderReceipt:
    """Provider acknowledgement, deliberately not an outcome proof."""

    operation_id: str
    provider: str
    received_at: datetime
    details: Mapping[str, Any]
    receipt_hash: str
    receipt_version: str = "provider-receipt/v1"

    @classmethod
    def capture(
        cls,
        *,
        operation_id: str,
        provider: str,
        received_at: datetime,
        details: Mapping[str, Any],
    ) -> "ProviderReceipt":
        if not operation_id or not provider:
            raise ProtocolViolation(
                "provider receipt operation_id/provider are required"
            )
        _require_aware(received_at, "received_at")
        details_snapshot = _snapshot(details)
        provisional = cls(
            operation_id=operation_id,
            provider=provider,
            received_at=received_at,
            details=details_snapshot,
            receipt_hash="",
        )
        return cls(
            operation_id=operation_id,
            provider=provider,
            received_at=received_at,
            details=details_snapshot,
            receipt_hash=canonical_digest(
                provisional,
                exclude=("receipt_hash",),
            ),
        )

    def verify(self) -> None:
        _require_aware(self.received_at, "received_at")
        actual = canonical_digest(
            self,
            exclude=("receipt_hash",),
        )
        if actual != self.receipt_hash:
            raise ProtocolViolation(
                "provider receipt digest mismatch: "
                f"expected {self.receipt_hash}, got {actual}"
            )


class ReconciliationStatus(str, Enum):
    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ReconciliationResult:
    status: ReconciliationStatus
    attempt_id: str
    operation_id: str
    observed_after_hash: str | None
    reason: str
    details: Mapping[str, Any]
    reconciled_at: datetime
    result_hash: str
    result_version: str = "reconciliation-result/v1"

    @classmethod
    def seal(
        cls,
        *,
        status: ReconciliationStatus,
        attempt_id: str,
        operation_id: str,
        observed_after: ObservationSnapshot | None,
        reason: str,
        details: Mapping[str, Any],
        reconciled_at: datetime,
    ) -> "ReconciliationResult":
        if not attempt_id or not operation_id or not reason:
            raise ProtocolViolation(
                "reconciliation attempt/operation/reason are required"
            )
        _require_aware(reconciled_at, "reconciled_at")
        if observed_after is not None:
            observed_after.verify()
        detail_snapshot = _snapshot(details)
        provisional = cls(
            status=status,
            attempt_id=attempt_id,
            operation_id=operation_id,
            observed_after_hash=(
                observed_after.observation_hash
                if observed_after is not None
                else None
            ),
            reason=reason,
            details=detail_snapshot,
            reconciled_at=reconciled_at,
            result_hash="",
        )
        return cls(
            status=provisional.status,
            attempt_id=provisional.attempt_id,
            operation_id=provisional.operation_id,
            observed_after_hash=provisional.observed_after_hash,
            reason=provisional.reason,
            details=provisional.details,
            reconciled_at=provisional.reconciled_at,
            result_hash=canonical_digest(
                provisional,
                exclude=("result_hash",),
            ),
        )

    def verify(self) -> None:
        _require_aware(self.reconciled_at, "reconciled_at")
        actual = canonical_digest(
            self,
            exclude=("result_hash",),
        )
        if actual != self.result_hash:
            raise ProtocolViolation(
                "reconciliation result digest mismatch: "
                f"expected {self.result_hash}, got {actual}"
            )


class VerificationStatus(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class VerificationCondition:
    condition_type: str
    status: VerificationStatus
    reason: str
    message: str
    evidence_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.condition_type or not self.reason:
            raise ProtocolViolation(
                "verification condition type/reason are required"
            )


@dataclass(frozen=True)
class VerificationReport:
    """Independent postcondition proof; not a provider acknowledgement."""

    plan_hash: str
    after_observation_hash: str
    conditions: tuple[VerificationCondition, ...]
    verifier: Principal
    verified_at: datetime
    report_hash: str
    report_version: str = "verification-report/v1"

    @classmethod
    def seal(
        cls,
        *,
        plan: TransitionPlan,
        after_observation: ObservationSnapshot,
        conditions: tuple[VerificationCondition, ...],
        verifier: Principal,
        verified_at: datetime,
    ) -> "VerificationReport":
        plan.verify()
        after_observation.verify()
        _require_aware(verified_at, "verified_at")
        if not conditions:
            raise ProtocolViolation(
                "verification report requires at least one condition"
            )
        types = [condition.condition_type for condition in conditions]
        if len(set(types)) != len(types):
            raise ProtocolViolation(
                "verification condition types must be unique"
            )
        canonical_conditions = tuple(
            sorted(
                conditions,
                key=lambda condition: condition.condition_type,
            )
        )
        provisional = cls(
            plan_hash=plan.plan_hash,
            after_observation_hash=after_observation.observation_hash,
            conditions=canonical_conditions,
            verifier=verifier,
            verified_at=verified_at,
            report_hash="",
        )
        return cls(
            plan_hash=provisional.plan_hash,
            after_observation_hash=provisional.after_observation_hash,
            conditions=provisional.conditions,
            verifier=provisional.verifier,
            verified_at=provisional.verified_at,
            report_hash=canonical_digest(
                provisional,
                exclude=("report_hash",),
            ),
        )

    @property
    def succeeded(self) -> bool:
        return all(
            condition.status is VerificationStatus.TRUE
            for condition in self.conditions
        )

    @property
    def has_unknown(self) -> bool:
        return any(
            condition.status is VerificationStatus.UNKNOWN
            for condition in self.conditions
        )

    def verify(
        self,
        *,
        plan: TransitionPlan | None = None,
        after_observation: ObservationSnapshot | None = None,
    ) -> None:
        _require_aware(self.verified_at, "verified_at")
        if not self.conditions:
            raise ProtocolViolation(
                "verification report requires at least one condition"
            )
        types = [condition.condition_type for condition in self.conditions]
        if len(set(types)) != len(types):
            raise ProtocolViolation(
                "verification condition types must be unique"
            )
        if tuple(
            sorted(
                self.conditions,
                key=lambda condition: condition.condition_type,
            )
        ) != self.conditions:
            raise ProtocolViolation(
                "verification conditions must be canonical-sorted"
            )
        actual = canonical_digest(
            self,
            exclude=("report_hash",),
        )
        if actual != self.report_hash:
            raise ProtocolViolation(
                "verification report digest mismatch: "
                f"expected {self.report_hash}, got {actual}"
            )
        if plan is not None:
            plan.verify()
            if plan.plan_hash != self.plan_hash:
                raise ProtocolViolation(
                    "verification report plan binding mismatch"
                )
        if after_observation is not None:
            after_observation.verify()
            if (
                after_observation.observation_hash
                != self.after_observation_hash
            ):
                raise ProtocolViolation(
                    "verification report observation binding mismatch"
                )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "VerificationReport":
        verifier = value.get("verifier")
        condition_values = value.get("conditions")
        if not isinstance(verifier, Mapping) or not isinstance(
            condition_values,
            list,
        ):
            raise ProtocolViolation(
                "verification report verifier/conditions are required"
            )
        conditions = []
        try:
            for item in condition_values:
                if not isinstance(item, Mapping):
                    raise TypeError("condition must be mapping")
                conditions.append(
                    VerificationCondition(
                        condition_type=str(item["condition_type"]),
                        status=VerificationStatus(str(item["status"])),
                        reason=str(item["reason"]),
                        message=str(item["message"]),
                        evidence_digest=(
                            str(item["evidence_digest"])
                            if item.get("evidence_digest") is not None
                            else None
                        ),
                    )
                )
            report = cls(
                plan_hash=str(value["plan_hash"]),
                after_observation_hash=str(
                    value["after_observation_hash"]
                ),
                conditions=tuple(conditions),
                verifier=_principal_from_mapping(verifier),
                verified_at=datetime.fromisoformat(
                    str(value["verified_at"])
                ),
                report_hash=str(value["report_hash"]),
                report_version=str(
                    value.get(
                        "report_version",
                        "verification-report/v1",
                    )
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolViolation(
                "invalid verification report mapping"
            ) from exc
        report.verify()
        return report

    def as_mapping(self) -> dict[str, Any]:
        self.verify()
        return {
            "plan_hash": self.plan_hash,
            "after_observation_hash": self.after_observation_hash,
            "conditions": [
                {
                    "condition_type": item.condition_type,
                    "status": item.status.value,
                    "reason": item.reason,
                    "message": item.message,
                    "evidence_digest": item.evidence_digest,
                }
                for item in self.conditions
            ],
            "verifier": _principal_mapping(self.verifier),
            "verified_at": self.verified_at.isoformat(),
            "report_hash": self.report_hash,
            "report_version": self.report_version,
        }


class MutationProvider(Protocol):
    """Semantic provider boundary; authority remains outside the provider."""

    def observe(
        self,
        subject: ResourceIdentity,
    ) -> ObservationSnapshot:
        ...

    def plan(
        self,
        observation: ObservationSnapshot,
        desired: Mapping[str, Any],
    ) -> TransitionPlan:
        ...

    def apply(
        self,
        plan: TransitionPlan,
        *,
        operation_id: str,
    ) -> ProviderReceipt:
        ...

    def reconcile(
        self,
        plan: TransitionPlan,
        *,
        attempt_id: str,
        operation_id: str,
        fresh_observation: ObservationSnapshot,
    ) -> ReconciliationResult:
        ...
