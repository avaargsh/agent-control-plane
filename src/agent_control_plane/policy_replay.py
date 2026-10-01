from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from .state_transition_protocol import (
    ActionIntent,
    EvidenceBundle,
    Principal,
    ProtocolViolation,
    StateTransition,
    canonical_digest,
)


class PolicyReplayMismatch(ProtocolViolation):
    """Historical policy replay diverged from the sealed decision."""


class PolicyEffect(str, Enum):
    PERMIT = "PERMIT"
    DENY = "DENY"


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolViolation(
            "policy context must be JSON-serializable"
        ) from exc


@dataclass(frozen=True)
class TransitionPolicyInput:
    policy_version: str
    evidence_hash: str
    transition_hash: str
    action_hash: str
    principal: Principal
    context_json: str
    input_hash: str
    input_version: str = "transition-policy-input/v1"

    @classmethod
    def seal(
        cls,
        *,
        policy_version: str,
        evidence: EvidenceBundle,
        transition: StateTransition,
        action: ActionIntent,
        principal: Principal,
        context: Mapping[str, Any],
    ) -> "TransitionPolicyInput":
        if not policy_version:
            raise ProtocolViolation("policy_version is required")
        evidence.verify()
        transition.verify()
        action.verify()
        if transition.evidence_hash != evidence.manifest_hash:
            raise ProtocolViolation(
                "policy input evidence is not bound to transition"
            )
        if action.transition_hash != transition.transition_hash:
            raise ProtocolViolation(
                "policy input action is not bound to transition"
            )
        context_json = _canonical_json(context)
        provisional = cls(
            policy_version=policy_version,
            evidence_hash=evidence.manifest_hash,
            transition_hash=transition.transition_hash,
            action_hash=action.action_hash,
            principal=principal,
            context_json=context_json,
            input_hash="",
        )
        return cls(
            policy_version=policy_version,
            evidence_hash=evidence.manifest_hash,
            transition_hash=transition.transition_hash,
            action_hash=action.action_hash,
            principal=principal,
            context_json=context_json,
            input_hash=canonical_digest(
                provisional,
                exclude=("input_hash",),
            ),
        )

    @property
    def context(self) -> Mapping[str, Any]:
        value = json.loads(self.context_json)
        if not isinstance(value, Mapping):
            raise ProtocolViolation("policy context must decode to an object")
        return value

    def verify(self) -> None:
        if not self.policy_version:
            raise ProtocolViolation("policy_version is required")
        canonical_context = _canonical_json(self.context)
        if canonical_context != self.context_json:
            raise ProtocolViolation("policy context is not canonical")
        actual = canonical_digest(self, exclude=("input_hash",))
        if actual != self.input_hash:
            raise ProtocolViolation(
                "policy input digest mismatch: "
                f"expected {self.input_hash}, got {actual}"
            )


@dataclass(frozen=True)
class PolicyEvaluation:
    effect: PolicyEffect
    reasons: tuple[str, ...] = ()


class TransitionPolicyEvaluator(Protocol):
    policy_version: str

    def evaluate(
        self,
        policy_input: TransitionPolicyInput,
    ) -> PolicyEvaluation:
        ...


@dataclass(frozen=True)
class PolicyDecisionRecord:
    policy_version: str
    input_hash: str
    effect: PolicyEffect
    reasons: tuple[str, ...]
    decision_hash: str
    record_version: str = "policy-decision/v1"

    @classmethod
    def seal(
        cls,
        *,
        policy_input: TransitionPolicyInput,
        evaluation: PolicyEvaluation,
    ) -> "PolicyDecisionRecord":
        policy_input.verify()
        provisional = cls(
            policy_version=policy_input.policy_version,
            input_hash=policy_input.input_hash,
            effect=evaluation.effect,
            reasons=evaluation.reasons,
            decision_hash="",
        )
        return cls(
            policy_version=policy_input.policy_version,
            input_hash=policy_input.input_hash,
            effect=evaluation.effect,
            reasons=evaluation.reasons,
            decision_hash=canonical_digest(
                provisional,
                exclude=("decision_hash",),
            ),
        )

    def verify(self) -> None:
        actual = canonical_digest(
            self,
            exclude=("decision_hash",),
        )
        if actual != self.decision_hash:
            raise ProtocolViolation(
                "policy decision digest mismatch: "
                f"expected {self.decision_hash}, got {actual}"
            )


def evaluate_policy(
    *,
    policy_input: TransitionPolicyInput,
    evaluator: TransitionPolicyEvaluator,
) -> PolicyDecisionRecord:
    policy_input.verify()
    if evaluator.policy_version != policy_input.policy_version:
        raise ProtocolViolation(
            "policy evaluator version does not match frozen policy input"
        )
    evaluation = evaluator.evaluate(policy_input)
    if not isinstance(evaluation, PolicyEvaluation):
        raise ProtocolViolation(
            "policy evaluator must return PolicyEvaluation"
        )
    return PolicyDecisionRecord.seal(
        policy_input=policy_input,
        evaluation=evaluation,
    )


def replay_policy_decision(
    *,
    original: PolicyDecisionRecord,
    policy_input: TransitionPolicyInput,
    evaluator: TransitionPolicyEvaluator,
) -> PolicyDecisionRecord:
    """Deterministically replay one frozen policy decision.

    Historical reconstruction is valid only when the exact frozen policy
    input and policy version are reused. The replayed decision hash must match
    the sealed original byte-for-byte at the protocol level.
    """

    original.verify()
    policy_input.verify()

    if original.policy_version != policy_input.policy_version:
        raise PolicyReplayMismatch(
            "policy version changed during replay"
        )
    if original.input_hash != policy_input.input_hash:
        raise PolicyReplayMismatch(
            "policy input changed during replay"
        )

    replayed = evaluate_policy(
        policy_input=policy_input,
        evaluator=evaluator,
    )
    if replayed.decision_hash != original.decision_hash:
        raise PolicyReplayMismatch(
            "policy replay decision diverged from sealed decision"
        )
    return replayed


@dataclass(frozen=True)
class DeploymentScalePolicy:
    """Small deterministic reference policy for the first vertical slice."""

    policy_version: str
    max_replicas: int
    allowed_namespaces: tuple[str, ...]

    def evaluate(
        self,
        policy_input: TransitionPolicyInput,
    ) -> PolicyEvaluation:
        context = policy_input.context
        reasons: list[str] = []

        if context.get("operation") != "scale_deployment":
            reasons.append("OPERATION_NOT_ALLOWED")

        namespace = context.get("namespace")
        if namespace not in self.allowed_namespaces:
            reasons.append("NAMESPACE_NOT_ALLOWED")

        replicas = context.get("replicas")
        if (
            isinstance(replicas, bool)
            or not isinstance(replicas, int)
            or replicas < 0
        ):
            reasons.append("REPLICAS_INVALID")
        elif replicas > self.max_replicas:
            reasons.append("REPLICA_LIMIT_EXCEEDED")

        if reasons:
            return PolicyEvaluation(
                effect=PolicyEffect.DENY,
                reasons=tuple(reasons),
            )
        return PolicyEvaluation(
            effect=PolicyEffect.PERMIT,
            reasons=("POLICY_ALLOWED",),
        )
