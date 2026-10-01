from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .execution_journal import (
    ExecutionAttempt,
    ExecutionAttemptState,
)
from .execution_verification import (
    OutcomeVerificationResult,
    VerificationStatus,
)
from .state_transition_protocol import (
    EvidenceBundle,
    OutcomeContract,
    ProtocolViolation,
    StateTransition,
    canonical_digest,
)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class ExecutionAttestation:
    """Provider-independent closure proof for one committed execution.

    The attestation binds the durable execution receipt to the independently
    observed post-state verification. For context-bound attempts it also carries
    the durable context provenance hash already sealed into the journal.
    """

    attestation_id: str
    attempt_id: str
    operation_id: str
    resource_uid: str
    transition_hash: str
    action_hash: str
    authorization_hash: str
    attempt_hash: str
    terminal_result_hash: str
    context_provenance_hash: str | None
    outcome_contract_hash: str
    observation_evidence_hash: str
    verification_status: VerificationStatus
    verification_hash: str
    verified_at: datetime
    attestation_hash: str
    attestation_version: str = "execution-attestation/v1"

    @classmethod
    def seal(
        cls,
        *,
        attestation_id: str,
        attempt: ExecutionAttempt,
        transition: StateTransition,
        outcome_contract: OutcomeContract,
        observation_evidence: EvidenceBundle,
        verification: OutcomeVerificationResult,
    ) -> "ExecutionAttestation":
        if not attestation_id:
            raise ProtocolViolation("attestation_id is required")
        transition.verify()
        outcome_contract.verify()
        observation_evidence.verify()
        verification.verify()
        if verification.status is not VerificationStatus.SUCCEEDED:
            raise ProtocolViolation(
                "execution attestation requires SUCCEEDED verification"
            )

        attempt.verify()
        if attempt.state is not ExecutionAttemptState.COMMITTED:
            raise ProtocolViolation(
                "execution attestation requires COMMITTED attempt"
            )
        if attempt.result_hash is None:
            raise ProtocolViolation(
                "execution attestation requires terminal result hash"
            )
        if attempt.transition_hash != transition.transition_hash:
            raise ProtocolViolation(
                "attestation transition does not match execution attempt"
            )
        if transition.outcome_contract_hash != outcome_contract.contract_hash:
            raise ProtocolViolation(
                "attestation outcome contract does not match transition"
            )
        if observation_evidence.resource != transition.subject:
            raise ProtocolViolation(
                "attestation observation does not match transition resource"
            )
        if observation_evidence.resource.resource_uid != attempt.resource_uid:
            raise ProtocolViolation(
                "attestation observation resource does not match execution"
            )
        if verification.transition_hash != transition.transition_hash:
            raise ProtocolViolation(
                "attestation verification transition mismatch"
            )
        if (
            verification.outcome_contract_hash
            != outcome_contract.contract_hash
        ):
            raise ProtocolViolation(
                "attestation verification outcome contract mismatch"
            )
        if (
            verification.observation_hash
            != observation_evidence.manifest_hash
        ):
            raise ProtocolViolation(
                "attestation verification observation mismatch"
            )

        provisional = cls(
            attestation_id=attestation_id,
            attempt_id=attempt.attempt_id,
            operation_id=attempt.operation_id,
            resource_uid=attempt.resource_uid,
            transition_hash=attempt.transition_hash,
            action_hash=attempt.action_hash,
            authorization_hash=attempt.authorization_hash,
            attempt_hash=attempt.attempt_hash,
            terminal_result_hash=attempt.result_hash,
            context_provenance_hash=attempt.context_provenance_hash,
            outcome_contract_hash=outcome_contract.contract_hash,
            observation_evidence_hash=observation_evidence.manifest_hash,
            verification_status=verification.status,
            verification_hash=verification.verification_hash,
            verified_at=verification.checked_at,
            attestation_hash="",
        )
        return cls(
            attestation_id=provisional.attestation_id,
            attempt_id=provisional.attempt_id,
            operation_id=provisional.operation_id,
            resource_uid=provisional.resource_uid,
            transition_hash=provisional.transition_hash,
            action_hash=provisional.action_hash,
            authorization_hash=provisional.authorization_hash,
            attempt_hash=provisional.attempt_hash,
            terminal_result_hash=provisional.terminal_result_hash,
            context_provenance_hash=provisional.context_provenance_hash,
            outcome_contract_hash=provisional.outcome_contract_hash,
            observation_evidence_hash=provisional.observation_evidence_hash,
            verification_status=provisional.verification_status,
            verification_hash=provisional.verification_hash,
            verified_at=provisional.verified_at,
            attestation_hash=canonical_digest(
                provisional,
                exclude=("attestation_hash",),
            ),
        )

    def verify(self) -> None:
        if not self.attestation_id:
            raise ProtocolViolation("attestation_id is required")
        if not all(
            (
                self.attempt_id,
                self.operation_id,
                self.resource_uid,
                self.transition_hash,
                self.action_hash,
                self.authorization_hash,
                self.attempt_hash,
                self.terminal_result_hash,
                self.outcome_contract_hash,
                self.observation_evidence_hash,
                self.verification_status.value,
                self.verification_hash,
            )
        ):
            raise ProtocolViolation(
                "execution attestation binding fields are required"
            )
        if not isinstance(self.verification_status, VerificationStatus):
            raise ProtocolViolation(
                "attestation verification status is invalid"
            )
        if self.verification_status is not VerificationStatus.SUCCEEDED:
            raise ProtocolViolation(
                "execution attestation requires SUCCEEDED verification"
            )
        _require_aware(self.verified_at, "verified_at")
        actual = canonical_digest(
            self,
            exclude=("attestation_hash",),
        )
        if actual != self.attestation_hash:
            raise ProtocolViolation(
                "execution attestation digest mismatch"
            )

    def verify_attempt(self, attempt: ExecutionAttempt) -> None:
        self.verify()
        attempt.verify()
        if attempt.state is not ExecutionAttemptState.COMMITTED:
            raise ProtocolViolation(
                "attested execution attempt is not COMMITTED"
            )
        expected = {
            "attempt_id": self.attempt_id,
            "operation_id": self.operation_id,
            "resource_uid": self.resource_uid,
            "transition_hash": self.transition_hash,
            "action_hash": self.action_hash,
            "authorization_hash": self.authorization_hash,
            "attempt_hash": self.attempt_hash,
            "terminal_result_hash": self.terminal_result_hash,
            "context_provenance_hash": self.context_provenance_hash,
        }
        actual = {
            "attempt_id": attempt.attempt_id,
            "operation_id": attempt.operation_id,
            "resource_uid": attempt.resource_uid,
            "transition_hash": attempt.transition_hash,
            "action_hash": attempt.action_hash,
            "authorization_hash": attempt.authorization_hash,
            "attempt_hash": attempt.attempt_hash,
            "terminal_result_hash": attempt.result_hash,
            "context_provenance_hash": attempt.context_provenance_hash,
        }
        if actual != expected:
            raise ProtocolViolation(
                "execution attestation attempt binding mismatch"
            )

    def verify_evidence(
        self,
        *,
        transition: StateTransition,
        outcome_contract: OutcomeContract,
        observation_evidence: EvidenceBundle,
        verification: OutcomeVerificationResult,
    ) -> None:
        self.verify()
        transition.verify()
        outcome_contract.verify()
        observation_evidence.verify()
        verification.verify()
        if transition.transition_hash != self.transition_hash:
            raise ProtocolViolation(
                "execution attestation transition binding mismatch"
            )
        if transition.outcome_contract_hash != outcome_contract.contract_hash:
            raise ProtocolViolation(
                "execution attestation transition outcome binding mismatch"
            )
        if outcome_contract.contract_hash != self.outcome_contract_hash:
            raise ProtocolViolation(
                "execution attestation outcome contract binding mismatch"
            )
        if (
            observation_evidence.manifest_hash
            != self.observation_evidence_hash
        ):
            raise ProtocolViolation(
                "execution attestation observation evidence binding mismatch"
            )
        if observation_evidence.resource != transition.subject:
            raise ProtocolViolation(
                "execution attestation observation resource mismatch"
            )
        if observation_evidence.resource.resource_uid != self.resource_uid:
            raise ProtocolViolation(
                "execution attestation observation execution mismatch"
            )
        if verification.transition_hash != self.transition_hash:
            raise ProtocolViolation(
                "execution attestation verification transition mismatch"
            )
        if (
            verification.outcome_contract_hash
            != self.outcome_contract_hash
        ):
            raise ProtocolViolation(
                "execution attestation verification outcome mismatch"
            )
        if verification.observation_hash != self.observation_evidence_hash:
            raise ProtocolViolation(
                "execution attestation verification observation mismatch"
            )
        if verification.status is not self.verification_status:
            raise ProtocolViolation(
                "execution attestation verification status mismatch"
            )
        if verification.verification_hash != self.verification_hash:
            raise ProtocolViolation(
                "execution attestation verification hash mismatch"
            )
        if verification.checked_at != self.verified_at:
            raise ProtocolViolation(
                "execution attestation verification time mismatch"
            )

    def as_mapping(self) -> dict[str, Any]:
        self.verify()
        return {
            "attestation_id": self.attestation_id,
            "attestation_version": self.attestation_version,
            "attempt_id": self.attempt_id,
            "operation_id": self.operation_id,
            "resource_uid": self.resource_uid,
            "transition_hash": self.transition_hash,
            "action_hash": self.action_hash,
            "authorization_hash": self.authorization_hash,
            "attempt_hash": self.attempt_hash,
            "terminal_result_hash": self.terminal_result_hash,
            "context_provenance_hash": self.context_provenance_hash,
            "outcome_contract_hash": self.outcome_contract_hash,
            "observation_evidence_hash": self.observation_evidence_hash,
            "verification_status": self.verification_status.value,
            "verification_hash": self.verification_hash,
            "verified_at": self.verified_at.isoformat(),
            "attestation_hash": self.attestation_hash,
        }
