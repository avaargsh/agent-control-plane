from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping

from .policy_replay import (
    PolicyDecisionRecord,
    PolicyEffect,
    TransitionPolicyInput,
)
from .state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    Principal,
    ProtocolViolation,
    StateTransition,
    canonical_digest,
)


class ApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    DENY = "DENY"


@dataclass(frozen=True)
class TransitionApproval:
    approval_id: str
    approver: Principal
    decision: ApprovalDecision
    execution_principal: Principal
    evidence_hash: str
    transition_hash: str
    action_hash: str
    policy_version: str
    policy_input_hash: str
    policy_decision_hash: str
    generation: int
    issued_at: datetime
    expires_at: datetime
    reason: str
    approval_hash: str
    approval_version: str = "transition-approval/v1"

    @classmethod
    def seal(
        cls,
        *,
        approval_id: str,
        approver: Principal,
        decision: ApprovalDecision,
        transition: StateTransition,
        action: ActionIntent,
        policy_input: TransitionPolicyInput,
        policy_decision: PolicyDecisionRecord,
        issued_at: datetime,
        expires_at: datetime,
        reason: str = "",
    ) -> "TransitionApproval":
        if not approval_id:
            raise ProtocolViolation("approval_id is required")
        _require_aware(issued_at, "issued_at")
        _require_aware(expires_at, "expires_at")
        if expires_at <= issued_at:
            raise ProtocolViolation(
                "approval expiry must follow issuance"
            )

        transition.verify()
        action.verify()
        policy_input.verify()
        policy_decision.verify()

        _verify_policy_chain(
            transition=transition,
            action=action,
            policy_input=policy_input,
            policy_decision=policy_decision,
        )

        provisional = cls(
            approval_id=approval_id,
            approver=approver,
            decision=decision,
            execution_principal=policy_input.principal,
            evidence_hash=transition.evidence_hash,
            transition_hash=transition.transition_hash,
            action_hash=action.action_hash,
            policy_version=policy_input.policy_version,
            policy_input_hash=policy_input.input_hash,
            policy_decision_hash=policy_decision.decision_hash,
            generation=transition.expected_generation,
            issued_at=issued_at,
            expires_at=expires_at,
            reason=reason,
            approval_hash="",
        )
        return cls(
            approval_id=approval_id,
            approver=approver,
            decision=decision,
            execution_principal=policy_input.principal,
            evidence_hash=transition.evidence_hash,
            transition_hash=transition.transition_hash,
            action_hash=action.action_hash,
            policy_version=policy_input.policy_version,
            policy_input_hash=policy_input.input_hash,
            policy_decision_hash=policy_decision.decision_hash,
            generation=transition.expected_generation,
            issued_at=issued_at,
            expires_at=expires_at,
            reason=reason,
            approval_hash=canonical_digest(
                provisional,
                exclude=("approval_hash",),
            ),
        )

    def verify(self) -> None:
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ProtocolViolation(
                "approval expiry must follow issuance"
            )
        actual = canonical_digest(
            self,
            exclude=("approval_hash",),
        )
        if actual != self.approval_hash:
            raise ProtocolViolation(
                "transition approval digest mismatch"
            )


@dataclass(frozen=True)
class ApprovalSigningKey:
    key_id: str
    approver: Principal
    secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.key_id:
            raise ProtocolViolation("approval key_id is required")
        if not self.secret:
            raise ProtocolViolation("approval signing secret is required")


@dataclass(frozen=True)
class SignedTransitionApproval:
    approval: TransitionApproval
    key_id: str
    algorithm: str
    signature: str

    @classmethod
    def sign(
        cls,
        approval: TransitionApproval,
        *,
        key: ApprovalSigningKey,
    ) -> "SignedTransitionApproval":
        approval.verify()
        if approval.approver != key.approver:
            raise ProtocolViolation(
                "approval signer identity does not match approver"
            )
        algorithm = "HMAC-SHA256"
        signature = hmac.new(
            key.secret,
            _signature_payload(
                key_id=key.key_id,
                approval_hash=approval.approval_hash,
            ),
            hashlib.sha256,
        ).hexdigest()
        return cls(
            approval=approval,
            key_id=key.key_id,
            algorithm=algorithm,
            signature=signature,
        )


@dataclass(frozen=True)
class HMACApprovalVerifier:
    """Reference approval authenticity verifier.

    HMAC is intentionally a local/reference mechanism. Production deployments
    should implement the same verification boundary with asymmetric KMS,
    Sigstore/Cosign, or another identity-backed signing system.
    """

    keys: Mapping[str, ApprovalSigningKey]

    def verify(
        self,
        signed: SignedTransitionApproval,
        *,
        now: datetime,
    ) -> TransitionApproval:
        _require_aware(now, "now")
        approval = signed.approval
        approval.verify()

        if signed.algorithm != "HMAC-SHA256":
            raise ProtocolViolation(
                "unsupported transition approval signature algorithm"
            )
        key = self.keys.get(signed.key_id)
        if key is None:
            raise ProtocolViolation(
                "transition approval signing key is not trusted"
            )
        if key.key_id != signed.key_id:
            raise ProtocolViolation(
                "transition approval trusted key id mismatch"
            )
        if key.approver != approval.approver:
            raise ProtocolViolation(
                "transition approval signer identity mismatch"
            )

        expected = hmac.new(
            key.secret,
            _signature_payload(
                key_id=signed.key_id,
                approval_hash=approval.approval_hash,
            ),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signed.signature):
            raise ProtocolViolation(
                "transition approval signature is invalid"
            )

        if now < approval.issued_at:
            raise ProtocolViolation(
                "transition approval is not yet valid"
            )
        if now >= approval.expires_at:
            raise ProtocolViolation(
                "transition approval expired"
            )
        return approval


def authorize_transition_from_approval(
    *,
    transition: StateTransition,
    action: ActionIntent,
    policy_input: TransitionPolicyInput,
    policy_decision: PolicyDecisionRecord,
    signed_approval: SignedTransitionApproval,
    approval_verifier: HMACApprovalVerifier,
    authorization_expires_at: datetime,
    now: datetime,
) -> AuthorizationBinding:
    """Create executable authorization only from an exact signed approval."""

    _require_aware(now, "now")
    _require_aware(
        authorization_expires_at,
        "authorization_expires_at",
    )
    transition.verify()
    action.verify()
    policy_input.verify()
    policy_decision.verify()

    _verify_policy_chain(
        transition=transition,
        action=action,
        policy_input=policy_input,
        policy_decision=policy_decision,
    )

    if policy_decision.effect is not PolicyEffect.PERMIT:
        raise ProtocolViolation(
            "human approval cannot override policy DENY"
        )

    approval = approval_verifier.verify(
        signed_approval,
        now=now,
    )
    if approval.decision is not ApprovalDecision.APPROVE:
        raise ProtocolViolation(
            "transition approval decision is not APPROVE"
        )

    expected = {
        "execution_principal": policy_input.principal,
        "evidence_hash": transition.evidence_hash,
        "transition_hash": transition.transition_hash,
        "action_hash": action.action_hash,
        "policy_version": policy_input.policy_version,
        "policy_input_hash": policy_input.input_hash,
        "policy_decision_hash": policy_decision.decision_hash,
        "generation": transition.expected_generation,
    }
    actual = {
        "execution_principal": approval.execution_principal,
        "evidence_hash": approval.evidence_hash,
        "transition_hash": approval.transition_hash,
        "action_hash": approval.action_hash,
        "policy_version": approval.policy_version,
        "policy_input_hash": approval.policy_input_hash,
        "policy_decision_hash": approval.policy_decision_hash,
        "generation": approval.generation,
    }
    if actual != expected:
        raise ProtocolViolation(
            "transition approval does not match authorization inputs"
        )

    if authorization_expires_at <= now:
        raise ProtocolViolation(
            "authorization expiry must be in the future"
        )
    if authorization_expires_at > approval.expires_at:
        raise ProtocolViolation(
            "authorization cannot outlive transition approval"
        )

    return AuthorizationBinding.seal(
        transition=transition,
        action=action,
        policy_version=policy_input.policy_version,
        policy_decision_hash=policy_decision.decision_hash,
        approval_hash=approval.approval_hash,
        principal=policy_input.principal,
        expires_at=authorization_expires_at,
    )


def _verify_policy_chain(
    *,
    transition: StateTransition,
    action: ActionIntent,
    policy_input: TransitionPolicyInput,
    policy_decision: PolicyDecisionRecord,
) -> None:
    if action.transition_hash != transition.transition_hash:
        raise ProtocolViolation(
            "approval action is not bound to transition"
        )
    if policy_input.evidence_hash != transition.evidence_hash:
        raise ProtocolViolation(
            "approval policy input evidence mismatch"
        )
    if policy_input.transition_hash != transition.transition_hash:
        raise ProtocolViolation(
            "approval policy input transition mismatch"
        )
    if policy_input.action_hash != action.action_hash:
        raise ProtocolViolation(
            "approval policy input action mismatch"
        )
    if policy_decision.policy_version != policy_input.policy_version:
        raise ProtocolViolation(
            "approval policy version mismatch"
        )
    if policy_decision.input_hash != policy_input.input_hash:
        raise ProtocolViolation(
            "approval policy decision input mismatch"
        )


def _signature_payload(
    *,
    key_id: str,
    approval_hash: str,
) -> bytes:
    return (
        "transition-approval/v1\x00"
        + key_id
        + "\x00"
        + approval_hash
    ).encode("utf-8")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(
            f"{field_name} must be timezone-aware"
        )
