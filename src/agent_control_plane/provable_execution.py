from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol

from .state_transition_protocol import (
    Principal,
    ProtocolViolation,
    ResourceIdentity,
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
