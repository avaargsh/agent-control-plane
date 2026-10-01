from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .state_transition_protocol import (
    ProtocolViolation,
    canonical_digest,
)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


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
    """Provider-neutral, hash-sealed outcome verification result."""

    status: VerificationStatus
    transition_hash: str
    outcome_contract_hash: str
    observation_hash: str
    checked_at: datetime
    desired: tuple[ConditionEvaluation, ...]
    safety: tuple[ConditionEvaluation, ...]
    reasons: tuple[str, ...]
    verification_hash: str
    verification_version: str = "outcome-verification/v1"

    @classmethod
    def seal(
        cls,
        *,
        status: VerificationStatus,
        transition_hash: str,
        outcome_contract_hash: str,
        observation_hash: str,
        checked_at: datetime,
        desired: tuple[ConditionEvaluation, ...],
        safety: tuple[ConditionEvaluation, ...],
        reasons: tuple[str, ...],
    ) -> "OutcomeVerificationResult":
        if not transition_hash:
            raise ProtocolViolation(
                "verification transition_hash is required"
            )
        if not outcome_contract_hash:
            raise ProtocolViolation(
                "verification outcome_contract_hash is required"
            )
        if not observation_hash:
            raise ProtocolViolation(
                "verification observation_hash is required"
            )
        _require_aware(checked_at, "checked_at")
        provisional = cls(
            status=status,
            transition_hash=transition_hash,
            outcome_contract_hash=outcome_contract_hash,
            observation_hash=observation_hash,
            checked_at=checked_at,
            desired=tuple(desired),
            safety=tuple(safety),
            reasons=tuple(reasons),
            verification_hash="",
        )
        return cls(
            status=provisional.status,
            transition_hash=provisional.transition_hash,
            outcome_contract_hash=provisional.outcome_contract_hash,
            observation_hash=provisional.observation_hash,
            checked_at=provisional.checked_at,
            desired=provisional.desired,
            safety=provisional.safety,
            reasons=provisional.reasons,
            verification_hash=canonical_digest(
                provisional,
                exclude=("verification_hash",),
            ),
        )

    def verify(self) -> None:
        if not isinstance(self.status, VerificationStatus):
            raise ProtocolViolation(
                "verification status must be a VerificationStatus"
            )
        if not all(
            (
                self.transition_hash,
                self.outcome_contract_hash,
                self.observation_hash,
            )
        ):
            raise ProtocolViolation(
                "verification binding fields are required"
            )
        _require_aware(self.checked_at, "checked_at")
        actual = canonical_digest(
            self,
            exclude=("verification_hash",),
        )
        if actual != self.verification_hash:
            raise ProtocolViolation(
                "outcome verification digest mismatch"
            )
