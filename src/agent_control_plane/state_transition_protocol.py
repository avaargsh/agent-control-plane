from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class ProtocolViolation(ValueError):
    """Raised when an immutable state-transition binding no longer verifies."""


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


def _snapshot_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolViolation("protocol payload must be JSON-serializable") from exc
    return json.loads(encoded)


def _canonicalize(value: Any) -> Any:
    if is_dataclass(value):
        return {
            field.name: _canonicalize(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, datetime):
        _require_aware(value, "datetime")
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ProtocolViolation(
        f"unsupported canonical value type: {type(value).__name__}"
    )


def canonical_digest(value: Any, *, exclude: tuple[str, ...] = ()) -> str:
    canonical = _canonicalize(value)
    if exclude:
        if not isinstance(canonical, dict):
            raise ProtocolViolation("digest exclusion requires an object value")
        canonical = {
            key: item
            for key, item in canonical.items()
            if key not in exclude
        }
    try:
        encoded = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolViolation("protocol value is not canonical JSON") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Principal:
    type: str
    subject: str

    def __post_init__(self) -> None:
        if not self.type or not self.subject:
            raise ProtocolViolation("principal type and subject are required")


@dataclass(frozen=True)
class ResourceIdentity:
    provider: str
    resource_uid: str
    namespace: str
    kind: str
    name: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.provider,
                self.resource_uid,
                self.namespace,
                self.kind,
                self.name,
            )
        ):
            raise ProtocolViolation("resource identity fields are required")


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    evidence_type: str
    source: str
    collected_at: datetime
    collector_name: str
    collector_version: str
    principal: Principal
    query: str | None
    payload: Mapping[str, Any]
    payload_hash: str

    @classmethod
    def capture(
        cls,
        *,
        evidence_id: str,
        evidence_type: str,
        source: str,
        collected_at: datetime,
        collector_name: str,
        collector_version: str,
        principal: Principal,
        payload: Mapping[str, Any],
        query: str | None = None,
    ) -> "EvidenceItem":
        _require_aware(collected_at, "collected_at")
        snapshot = _snapshot_mapping(payload)
        return cls(
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            source=source,
            collected_at=collected_at,
            collector_name=collector_name,
            collector_version=collector_version,
            principal=principal,
            query=query,
            payload=snapshot,
            payload_hash=canonical_digest(snapshot),
        )

    def verify(self) -> None:
        _require_aware(self.collected_at, "collected_at")
        if not all(
            (
                self.evidence_id,
                self.evidence_type,
                self.source,
                self.collector_name,
                self.collector_version,
            )
        ):
            raise ProtocolViolation("evidence provenance fields are required")
        actual = canonical_digest(self.payload)
        if actual != self.payload_hash:
            raise ProtocolViolation(
                "evidence payload digest mismatch: "
                f"expected {self.payload_hash}, got {actual}"
            )


@dataclass(frozen=True, order=True)
class EvidenceRef:
    evidence_id: str
    payload_hash: str


@dataclass(frozen=True)
class EvidenceBundle:
    resource: ResourceIdentity
    generation: int
    created_at: datetime
    items: tuple[EvidenceRef, ...]
    manifest_hash: str
    bundle_version: str = "evidence-bundle/v1"

    @classmethod
    def seal(
        cls,
        *,
        resource: ResourceIdentity,
        generation: int,
        created_at: datetime,
        items: tuple[EvidenceItem, ...],
    ) -> "EvidenceBundle":
        if generation < 0:
            raise ProtocolViolation("evidence generation cannot be negative")
        _require_aware(created_at, "created_at")
        if not items:
            raise ProtocolViolation("evidence bundle cannot be empty")
        for item in items:
            item.verify()
        refs = tuple(
            sorted(
                EvidenceRef(
                    evidence_id=item.evidence_id,
                    payload_hash=item.payload_hash,
                )
                for item in items
            )
        )
        provisional = cls(
            resource=resource,
            generation=generation,
            created_at=created_at,
            items=refs,
            manifest_hash="",
        )
        return cls(
            resource=resource,
            generation=generation,
            created_at=created_at,
            items=refs,
            manifest_hash=canonical_digest(
                provisional,
                exclude=("manifest_hash",),
            ),
        )

    def verify(
        self,
        item_lookup: Mapping[str, EvidenceItem] | None = None,
    ) -> None:
        _require_aware(self.created_at, "created_at")
        if self.generation < 0:
            raise ProtocolViolation("evidence generation cannot be negative")
        if not self.items:
            raise ProtocolViolation("evidence bundle cannot be empty")
        if tuple(sorted(self.items)) != self.items:
            raise ProtocolViolation("evidence refs must be canonical-sorted")
        actual = canonical_digest(self, exclude=("manifest_hash",))
        if actual != self.manifest_hash:
            raise ProtocolViolation(
                "evidence manifest digest mismatch: "
                f"expected {self.manifest_hash}, got {actual}"
            )
        if item_lookup is None:
            return
        for ref in self.items:
            item = item_lookup.get(ref.evidence_id)
            if item is None:
                raise ProtocolViolation(
                    f"evidence item missing: {ref.evidence_id}"
                )
            item.verify()
            if item.payload_hash != ref.payload_hash:
                raise ProtocolViolation(
                    f"evidence ref digest mismatch: {ref.evidence_id}"
                )


_ALLOWED_COMPARATORS = {"eq", "ne", "lt", "lte", "gt", "gte"}


@dataclass(frozen=True)
class OutcomeCondition:
    source: str
    expression: str
    comparator: str
    expected: str | int | float | bool

    def __post_init__(self) -> None:
        if not self.source or not self.expression:
            raise ProtocolViolation("outcome condition source/expression required")
        if self.comparator not in _ALLOWED_COMPARATORS:
            raise ProtocolViolation(
                f"unsupported outcome comparator: {self.comparator}"
            )


@dataclass(frozen=True)
class OutcomeContract:
    desired_conditions: tuple[OutcomeCondition, ...]
    safety_conditions: tuple[OutcomeCondition, ...]
    stabilization_seconds: int
    deadline_seconds: int
    contract_hash: str
    contract_version: str = "outcome-contract/v1"

    @classmethod
    def seal(
        cls,
        *,
        desired_conditions: tuple[OutcomeCondition, ...],
        safety_conditions: tuple[OutcomeCondition, ...] = (),
        stabilization_seconds: int,
        deadline_seconds: int,
    ) -> "OutcomeContract":
        if not desired_conditions:
            raise ProtocolViolation(
                "outcome contract requires a desired condition"
            )
        if stabilization_seconds < 0:
            raise ProtocolViolation(
                "stabilization_seconds cannot be negative"
            )
        if deadline_seconds <= 0:
            raise ProtocolViolation("deadline_seconds must be positive")
        if stabilization_seconds > deadline_seconds:
            raise ProtocolViolation(
                "stabilization window cannot exceed deadline"
            )
        provisional = cls(
            desired_conditions=desired_conditions,
            safety_conditions=safety_conditions,
            stabilization_seconds=stabilization_seconds,
            deadline_seconds=deadline_seconds,
            contract_hash="",
        )
        return cls(
            desired_conditions=desired_conditions,
            safety_conditions=safety_conditions,
            stabilization_seconds=stabilization_seconds,
            deadline_seconds=deadline_seconds,
            contract_hash=canonical_digest(
                provisional,
                exclude=("contract_hash",),
            ),
        )

    def verify(self) -> None:
        actual = canonical_digest(self, exclude=("contract_hash",))
        if actual != self.contract_hash:
            raise ProtocolViolation(
                "outcome contract digest mismatch: "
                f"expected {self.contract_hash}, got {actual}"
            )


@dataclass(frozen=True)
class StateTransition:
    transition_id: str
    subject: ResourceIdentity
    expected_generation: int
    before: Mapping[str, Any]
    desired: Mapping[str, Any]
    evidence_hash: str
    outcome_contract_hash: str
    created_at: datetime
    transition_hash: str
    transition_version: str = "state-transition/v1"

    @classmethod
    def seal(
        cls,
        *,
        transition_id: str,
        subject: ResourceIdentity,
        expected_generation: int,
        before: Mapping[str, Any],
        desired: Mapping[str, Any],
        evidence_hash: str,
        outcome_contract_hash: str,
        created_at: datetime,
    ) -> "StateTransition":
        if not transition_id:
            raise ProtocolViolation("transition_id is required")
        if expected_generation < 0:
            raise ProtocolViolation("expected_generation cannot be negative")
        _require_aware(created_at, "created_at")
        before_snapshot = _snapshot_mapping(before)
        desired_snapshot = _snapshot_mapping(desired)
        if before_snapshot == desired_snapshot:
            raise ProtocolViolation("state transition cannot be a no-op")
        provisional = cls(
            transition_id=transition_id,
            subject=subject,
            expected_generation=expected_generation,
            before=before_snapshot,
            desired=desired_snapshot,
            evidence_hash=evidence_hash,
            outcome_contract_hash=outcome_contract_hash,
            created_at=created_at,
            transition_hash="",
        )
        return cls(
            transition_id=transition_id,
            subject=subject,
            expected_generation=expected_generation,
            before=before_snapshot,
            desired=desired_snapshot,
            evidence_hash=evidence_hash,
            outcome_contract_hash=outcome_contract_hash,
            created_at=created_at,
            transition_hash=canonical_digest(
                provisional,
                exclude=("transition_hash",),
            ),
        )

    def verify(self) -> None:
        actual = canonical_digest(self, exclude=("transition_hash",))
        if actual != self.transition_hash:
            raise ProtocolViolation(
                "state transition digest mismatch: "
                f"expected {self.transition_hash}, got {actual}"
            )


@dataclass(frozen=True)
class ActionIntent:
    action_id: str
    transition_hash: str
    provider: str
    operation: str
    parameters: Mapping[str, Any]
    action_hash: str
    action_version: str = "action-intent/v1"

    @classmethod
    def seal(
        cls,
        *,
        action_id: str,
        transition_hash: str,
        provider: str,
        operation: str,
        parameters: Mapping[str, Any],
    ) -> "ActionIntent":
        if not all((action_id, transition_hash, provider, operation)):
            raise ProtocolViolation("action identity and operation are required")
        snapshot = _snapshot_mapping(parameters)
        provisional = cls(
            action_id=action_id,
            transition_hash=transition_hash,
            provider=provider,
            operation=operation,
            parameters=snapshot,
            action_hash="",
        )
        return cls(
            action_id=action_id,
            transition_hash=transition_hash,
            provider=provider,
            operation=operation,
            parameters=snapshot,
            action_hash=canonical_digest(
                provisional,
                exclude=("action_hash",),
            ),
        )

    def verify(self) -> None:
        actual = canonical_digest(self, exclude=("action_hash",))
        if actual != self.action_hash:
            raise ProtocolViolation(
                "action intent digest mismatch: "
                f"expected {self.action_hash}, got {actual}"
            )


@dataclass(frozen=True)
class AuthorizationBinding:
    transition_hash: str
    evidence_hash: str
    action_hash: str
    policy_version: str
    policy_decision_hash: str
    approval_hash: str | None
    generation: int
    principal: Principal
    expires_at: datetime
    authorization_hash: str
    binding_version: str = "authorization-binding/v1"

    @classmethod
    def seal(
        cls,
        *,
        transition: StateTransition,
        action: ActionIntent,
        policy_version: str,
        policy_decision_hash: str,
        approval_hash: str | None,
        principal: Principal,
        expires_at: datetime,
    ) -> "AuthorizationBinding":
        transition.verify()
        action.verify()
        if action.transition_hash != transition.transition_hash:
            raise ProtocolViolation("action is not bound to transition")
        _require_aware(expires_at, "expires_at")
        if not policy_version or not policy_decision_hash:
            raise ProtocolViolation("policy binding is required")
        provisional = cls(
            transition_hash=transition.transition_hash,
            evidence_hash=transition.evidence_hash,
            action_hash=action.action_hash,
            policy_version=policy_version,
            policy_decision_hash=policy_decision_hash,
            approval_hash=approval_hash,
            generation=transition.expected_generation,
            principal=principal,
            expires_at=expires_at,
            authorization_hash="",
        )
        return cls(
            transition_hash=transition.transition_hash,
            evidence_hash=transition.evidence_hash,
            action_hash=action.action_hash,
            policy_version=policy_version,
            policy_decision_hash=policy_decision_hash,
            approval_hash=approval_hash,
            generation=transition.expected_generation,
            principal=principal,
            expires_at=expires_at,
            authorization_hash=canonical_digest(
                provisional,
                exclude=("authorization_hash",),
            ),
        )

    def verify(self) -> None:
        _require_aware(self.expires_at, "expires_at")
        actual = canonical_digest(
            self,
            exclude=("authorization_hash",),
        )
        if actual != self.authorization_hash:
            raise ProtocolViolation(
                "authorization digest mismatch: "
                f"expected {self.authorization_hash}, got {actual}"
            )


@dataclass(frozen=True)
class ExecutionLease:
    lease_id: str
    resource_uid: str
    holder: Principal
    epoch: int
    acquired_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.lease_id or not self.resource_uid:
            raise ProtocolViolation("lease identity is required")
        if self.epoch <= 0:
            raise ProtocolViolation("lease epoch must be positive")
        _require_aware(self.acquired_at, "acquired_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.acquired_at:
            raise ProtocolViolation("lease expiry must follow acquisition")

    def assert_active(self, now: datetime) -> None:
        _require_aware(now, "now")
        if now >= self.expires_at:
            raise ProtocolViolation("execution lease expired")


@dataclass(frozen=True)
class ExecutionFence:
    resource_uid: str
    desired_generation: int
    lease_id: str
    lease_holder: Principal
    lease_epoch: int
    transition_hash: str
    action_hash: str
    authorization_hash: str
    expires_at: datetime

    @classmethod
    def bind(
        cls,
        *,
        transition: StateTransition,
        action: ActionIntent,
        authorization: AuthorizationBinding,
        lease: ExecutionLease,
    ) -> "ExecutionFence":
        transition.verify()
        action.verify()
        authorization.verify()
        if transition.subject.resource_uid != lease.resource_uid:
            raise ProtocolViolation("lease resource does not match transition")
        if action.transition_hash != transition.transition_hash:
            raise ProtocolViolation("action transition binding mismatch")
        if authorization.transition_hash != transition.transition_hash:
            raise ProtocolViolation("authorization transition binding mismatch")
        if authorization.evidence_hash != transition.evidence_hash:
            raise ProtocolViolation("authorization evidence binding mismatch")
        if authorization.action_hash != action.action_hash:
            raise ProtocolViolation("authorization action binding mismatch")
        if authorization.generation != transition.expected_generation:
            raise ProtocolViolation("authorization generation mismatch")
        return cls(
            resource_uid=transition.subject.resource_uid,
            desired_generation=transition.expected_generation,
            lease_id=lease.lease_id,
            lease_holder=lease.holder,
            lease_epoch=lease.epoch,
            transition_hash=transition.transition_hash,
            action_hash=action.action_hash,
            authorization_hash=authorization.authorization_hash,
            expires_at=min(
                authorization.expires_at,
                lease.expires_at,
            ),
        )


def validate_execution(
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
) -> None:
    """Fail closed immediately before the provider side effect."""

    _require_aware(now, "now")
    evidence.verify()
    outcome_contract.verify()
    transition.verify()
    action.verify()
    authorization.verify()
    active_lease.assert_active(now)

    if now >= authorization.expires_at:
        raise ProtocolViolation("authorization expired")
    if now >= fence.expires_at:
        raise ProtocolViolation("execution fence expired")

    if evidence.resource != transition.subject:
        raise ProtocolViolation("evidence resource does not match transition")
    if evidence.generation != transition.expected_generation:
        raise ProtocolViolation("evidence generation does not match transition")
    if transition.evidence_hash != evidence.manifest_hash:
        raise ProtocolViolation("transition evidence binding mismatch")
    if (
        transition.outcome_contract_hash
        != outcome_contract.contract_hash
    ):
        raise ProtocolViolation("transition outcome contract mismatch")

    if action.transition_hash != transition.transition_hash:
        raise ProtocolViolation("action transition binding mismatch")

    if authorization.transition_hash != transition.transition_hash:
        raise ProtocolViolation("authorization transition binding mismatch")
    if authorization.evidence_hash != evidence.manifest_hash:
        raise ProtocolViolation("authorization evidence binding mismatch")
    if authorization.action_hash != action.action_hash:
        raise ProtocolViolation("authorization action binding mismatch")
    if authorization.generation != transition.expected_generation:
        raise ProtocolViolation("authorization generation mismatch")

    if fence.resource_uid != transition.subject.resource_uid:
        raise ProtocolViolation("fence resource identity mismatch")
    if fence.desired_generation != transition.expected_generation:
        raise ProtocolViolation("fence generation binding mismatch")
    if fence.transition_hash != transition.transition_hash:
        raise ProtocolViolation("fence transition binding mismatch")
    if fence.action_hash != action.action_hash:
        raise ProtocolViolation("fence action binding mismatch")
    if fence.authorization_hash != authorization.authorization_hash:
        raise ProtocolViolation("fence authorization binding mismatch")

    if current_generation != transition.expected_generation:
        raise ProtocolViolation(
            "resource generation changed before execution"
        )

    if active_lease.resource_uid != transition.subject.resource_uid:
        raise ProtocolViolation("active lease resource mismatch")
    if active_lease.lease_id != fence.lease_id:
        raise ProtocolViolation("stale execution lease id")
    if active_lease.epoch != fence.lease_epoch:
        raise ProtocolViolation("stale execution lease epoch")
    if active_lease.holder != fence.lease_holder:
        raise ProtocolViolation("execution lease holder changed")
    if caller != fence.lease_holder:
        raise ProtocolViolation("caller does not hold execution lease")
