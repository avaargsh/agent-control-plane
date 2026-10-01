from __future__ import annotations

from typing import Any, Mapping


def normalize_recovery_evidence(
    evidence: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, float]]:
    if evidence is None:
        return None, {}

    if "success" not in evidence:
        raise ValueError("recovery evidence requires success")
    if "identityPreserved" not in evidence:
        raise ValueError("recovery evidence requires identityPreserved")

    success = evidence["success"]
    identity = evidence["identityPreserved"]
    if not isinstance(success, bool) or not isinstance(identity, bool):
        raise ValueError("recovery evidence flags must be boolean")

    normalized = {
        "success": success,
        "identityPreserved": identity,
        "sourceSandboxRef": evidence.get("sourceSandboxRef"),
        "targetSandboxRef": evidence.get("targetSandboxRef"),
        "snapshotRef": evidence.get("snapshotRef"),
        "verificationEvidenceRef": evidence.get("verificationEvidenceRef"),
        "verificationWindowSeconds": evidence.get("verificationWindowSeconds"),
    }
    metrics = {
        "recovery_success": 1.0 if success else 0.0,
        "recovery_identity_preserved": 1.0 if identity else 0.0,
    }
    return normalized, metrics


from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .state_transition_protocol import (
    EvidenceBundle,
    OutcomeContract,
    ProtocolViolation,
    StateTransition,
)


class RecoveryDisposition(str, Enum):
    RECOVER = "RECOVER"
    FREEZE = "FREEZE"
    NO_ACTION = "NO_ACTION"


@dataclass(frozen=True)
class RecoveryPolicy:
    name: str
    version: str
    recover_on: tuple[str, ...] = (
        "DEGRADED",
        "TIMED_OUT",
        "INVARIANT_VIOLATION",
    )
    freeze_on: tuple[str, ...] = ("UNKNOWN",)
    strategy: str = "restore_before"

    def __post_init__(self) -> None:
        if not self.name or not self.version:
            raise ProtocolViolation("recovery policy identity is required")
        if self.strategy != "restore_before":
            raise ProtocolViolation(
                f"unsupported recovery strategy: {self.strategy}"
            )
        overlap = set(self.recover_on) & set(self.freeze_on)
        if overlap:
            raise ProtocolViolation(
                "recovery policy cannot both recover and freeze the same status"
            )

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def decide(self, status: str) -> RecoveryDisposition:
        if status in self.recover_on:
            return RecoveryDisposition.RECOVER
        if status in self.freeze_on:
            return RecoveryDisposition.FREEZE
        return RecoveryDisposition.NO_ACTION


@dataclass(frozen=True)
class RecoveryPlan:
    disposition: RecoveryDisposition
    policy_ref: str
    source_transition_hash: str
    trigger_status: str
    failure_evidence_hash: str
    recovery_transition: StateTransition | None


def plan_recovery_transition(
    *,
    source_transition: StateTransition,
    trigger_status: str,
    failure_evidence: EvidenceBundle,
    outcome_contract: OutcomeContract,
    policy: RecoveryPolicy,
    created_at: datetime,
    transition_id: str,
) -> RecoveryPlan:
    """Turn a failed verification into a new semantic StateTransition.

    This function never authorizes or executes recovery. A RECOVER decision
    only creates a new transition. The caller must still create a new
    ActionIntent, AuthorizationBinding, ExecutionLease and ExecutionFence.
    """

    source_transition.verify()
    failure_evidence.verify()
    outcome_contract.verify()

    if failure_evidence.resource != source_transition.subject:
        raise ProtocolViolation(
            "recovery evidence resource does not match source transition"
        )

    disposition = policy.decide(trigger_status)
    if disposition is not RecoveryDisposition.RECOVER:
        return RecoveryPlan(
            disposition=disposition,
            policy_ref=policy.ref,
            source_transition_hash=source_transition.transition_hash,
            trigger_status=trigger_status,
            failure_evidence_hash=failure_evidence.manifest_hash,
            recovery_transition=None,
        )

    if failure_evidence.generation <= source_transition.expected_generation:
        raise ProtocolViolation(
            "recovery evidence must observe a generation after the source transition"
        )

    recovery_transition = StateTransition.seal(
        transition_id=transition_id,
        subject=source_transition.subject,
        expected_generation=failure_evidence.generation,
        before=source_transition.desired,
        desired=source_transition.before,
        evidence_hash=failure_evidence.manifest_hash,
        outcome_contract_hash=outcome_contract.contract_hash,
        created_at=created_at,
    )
    return RecoveryPlan(
        disposition=disposition,
        policy_ref=policy.ref,
        source_transition_hash=source_transition.transition_hash,
        trigger_status=trigger_status,
        failure_evidence_hash=failure_evidence.manifest_hash,
        recovery_transition=recovery_transition,
    )
