from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .execution_journal import (
    ExecutionAttempt,
    ExecutionAttemptState,
)
from .provable_execution import (
    ObservationSnapshot,
    PlanAuthorizationBinding,
    PlanExecutionFence,
    TransitionPlan,
    VerificationReport,
    VerificationStatus,
)
from .state_transition_protocol import ProtocolViolation, canonical_digest


_PREDICATE_TYPE = "agent-control-plane/state-transition-execution/v1"


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
            "independent execution proof must be canonical JSON"
        ) from exc
    return json.loads(encoded)


def _required_mapping(
    value: Mapping[str, Any],
    name: str,
) -> Mapping[str, Any]:
    item = value.get(name)
    if not isinstance(item, Mapping):
        raise ProtocolViolation(
            f"independent execution proof {name} mapping is required"
        )
    return item


def _validate_chain(
    *,
    plan: TransitionPlan,
    authorization: PlanAuthorizationBinding,
    fence: PlanExecutionFence,
    attempt: ExecutionAttempt,
    after_observation: ObservationSnapshot,
    verification: VerificationReport,
) -> None:
    plan.verify()
    authorization.verify()
    fence.verify()
    attempt.verify()
    after_observation.verify()
    verification.verify(
        plan=plan,
        after_observation=after_observation,
    )

    if attempt.state is not ExecutionAttemptState.COMMITTED:
        raise ProtocolViolation(
            "independent proof requires COMMITTED execution attempt"
        )
    if attempt.result_hash is None or attempt.result is None:
        raise ProtocolViolation(
            "independent proof requires terminal execution result"
        )

    if authorization.plan_hash != plan.plan_hash:
        raise ProtocolViolation(
            "independent proof plan authorization mismatch"
        )
    if fence.plan_hash != plan.plan_hash:
        raise ProtocolViolation(
            "independent proof plan fence mismatch"
        )
    if fence.plan_authorization_hash != authorization.binding_hash:
        raise ProtocolViolation(
            "independent proof fence authorization mismatch"
        )

    if attempt.resource_uid != plan.subject.resource_uid:
        raise ProtocolViolation(
            "independent proof attempt resource mismatch"
        )
    if attempt.transition_plan_hash != plan.plan_hash:
        raise ProtocolViolation(
            "independent proof attempt plan mismatch"
        )
    if attempt.plan_authorization_hash != authorization.binding_hash:
        raise ProtocolViolation(
            "independent proof attempt authorization mismatch"
        )
    if attempt.plan_fence_hash != fence.fence_hash:
        raise ProtocolViolation(
            "independent proof attempt fence mismatch"
        )
    if attempt.transition_hash != authorization.transition_hash:
        raise ProtocolViolation(
            "independent proof transition binding mismatch"
        )
    if attempt.action_hash != authorization.action_hash:
        raise ProtocolViolation(
            "independent proof action binding mismatch"
        )
    if (
        attempt.authorization_hash
        != authorization.source_authorization_hash
    ):
        raise ProtocolViolation(
            "independent proof source authorization mismatch"
        )
    if attempt.lease_id != fence.lease_id:
        raise ProtocolViolation(
            "independent proof lease id mismatch"
        )
    if attempt.lease_epoch != fence.lease_epoch:
        raise ProtocolViolation(
            "independent proof lease epoch mismatch"
        )

    result = attempt.result
    if result.get("plan_hash") != plan.plan_hash:
        raise ProtocolViolation(
            "independent proof provider result plan mismatch"
        )
    embedded_plan = result.get("_transition_plan")
    if embedded_plan != plan.as_mapping():
        raise ProtocolViolation(
            "independent proof terminal plan artifact mismatch"
        )

    if after_observation.subject != plan.subject:
        raise ProtocolViolation(
            "independent proof after-observation subject mismatch"
        )
    if verification.plan_hash != plan.plan_hash:
        raise ProtocolViolation(
            "independent proof verification plan mismatch"
        )
    if (
        verification.after_observation_hash
        != after_observation.observation_hash
    ):
        raise ProtocolViolation(
            "independent proof verification observation mismatch"
        )
    if not verification.succeeded or verification.has_unknown:
        raise ProtocolViolation(
            "independent proof requires fully successful verification"
        )

    conditions = {
        item.condition_type: item.status
        for item in verification.conditions
    }
    for required in (
        "DesiredStateReached",
        "OperationOwnershipProven",
    ):
        if conditions.get(required) is not VerificationStatus.TRUE:
            raise ProtocolViolation(
                "independent proof requires TRUE "
                f"{required} condition"
            )


@dataclass(frozen=True)
class IndependentExecutionProof:
    """In-toto-shaped, unsigned proof over canonical execution artifacts.

    The proof is intentionally independent of an Agent session, workflow
    history, hidden reasoning, or the original process. Authenticity is not
    invented here: deployments may sign the canonical statement hash using
    Sigstore/KMS/DSSE or compare it with another trusted digest source.
    """

    proof_id: str
    subject: Mapping[str, Any]
    predicate_type: str
    predicate: Mapping[str, Any]
    statement_hash: str
    statement_version: str = "independent-execution-proof/v1"

    @classmethod
    def seal(
        cls,
        *,
        proof_id: str,
        plan: TransitionPlan,
        authorization: PlanAuthorizationBinding,
        fence: PlanExecutionFence,
        attempt: ExecutionAttempt,
        after_observation: ObservationSnapshot,
        verification: VerificationReport,
    ) -> "IndependentExecutionProof":
        if not proof_id:
            raise ProtocolViolation("independent proof_id is required")

        _validate_chain(
            plan=plan,
            authorization=authorization,
            fence=fence,
            attempt=attempt,
            after_observation=after_observation,
            verification=verification,
        )

        execution_record_digest = canonical_digest(
            {
                "attempt_hash": attempt.attempt_hash,
                "terminal_result_hash": attempt.result_hash,
                "verification_report_hash": verification.report_hash,
            }
        )
        subject = {
            "name": f"execution/{attempt.attempt_id}",
            "digest": {
                "sha256": execution_record_digest,
            },
        }
        predicate = {
            "transition_plan": plan.as_mapping(),
            "plan_authorization": authorization.as_mapping(),
            "plan_fence": fence.as_mapping(),
            "execution_attempt": attempt.as_mapping(),
            "after_observation": after_observation.as_mapping(),
            "verification_report": verification.as_mapping(),
        }
        provisional = cls(
            proof_id=proof_id,
            subject=_snapshot(subject),
            predicate_type=_PREDICATE_TYPE,
            predicate=_snapshot(predicate),
            statement_hash="",
        )
        return cls(
            proof_id=provisional.proof_id,
            subject=provisional.subject,
            predicate_type=provisional.predicate_type,
            predicate=provisional.predicate,
            statement_hash=canonical_digest(
                provisional,
                exclude=("statement_hash",),
            ),
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "IndependentExecutionProof":
        subject = _required_mapping(value, "subject")
        predicate = _required_mapping(value, "predicate")
        try:
            proof = cls(
                proof_id=str(value["proof_id"]),
                subject=_snapshot(subject),
                predicate_type=str(value["predicate_type"]),
                predicate=_snapshot(predicate),
                statement_hash=str(value["statement_hash"]),
                statement_version=str(
                    value.get(
                        "statement_version",
                        "independent-execution-proof/v1",
                    )
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolViolation(
                "invalid independent execution proof mapping"
            ) from exc
        proof.verify()
        return proof

    def verify(self) -> None:
        if not self.proof_id:
            raise ProtocolViolation("independent proof_id is required")
        if self.predicate_type != _PREDICATE_TYPE:
            raise ProtocolViolation(
                "unsupported independent proof predicate type"
            )
        actual = canonical_digest(
            self,
            exclude=("statement_hash",),
        )
        if actual != self.statement_hash:
            raise ProtocolViolation(
                "independent execution proof digest mismatch"
            )

        plan = TransitionPlan.from_mapping(
            _required_mapping(self.predicate, "transition_plan")
        )
        authorization = PlanAuthorizationBinding.from_mapping(
            _required_mapping(self.predicate, "plan_authorization")
        )
        fence = PlanExecutionFence.from_mapping(
            _required_mapping(self.predicate, "plan_fence")
        )
        attempt = ExecutionAttempt.from_mapping(
            _required_mapping(self.predicate, "execution_attempt")
        )
        after_observation = ObservationSnapshot.from_mapping(
            _required_mapping(self.predicate, "after_observation")
        )
        verification = VerificationReport.from_mapping(
            _required_mapping(self.predicate, "verification_report")
        )

        _validate_chain(
            plan=plan,
            authorization=authorization,
            fence=fence,
            attempt=attempt,
            after_observation=after_observation,
            verification=verification,
        )

        expected_subject = {
            "name": f"execution/{attempt.attempt_id}",
            "digest": {
                "sha256": canonical_digest(
                    {
                        "attempt_hash": attempt.attempt_hash,
                        "terminal_result_hash": attempt.result_hash,
                        "verification_report_hash": verification.report_hash,
                    }
                )
            },
        }
        if self.subject != expected_subject:
            raise ProtocolViolation(
                "independent execution proof subject mismatch"
            )

    def as_mapping(self) -> dict[str, Any]:
        self.verify()
        return {
            "proof_id": self.proof_id,
            "subject": dict(self.subject),
            "predicate_type": self.predicate_type,
            "predicate": dict(self.predicate),
            "statement_hash": self.statement_hash,
            "statement_version": self.statement_version,
        }


def verify_execution_proof_mapping(
    value: Mapping[str, Any],
    *,
    expected_statement_hash: str | None = None,
) -> IndependentExecutionProof:
    """Verify serialized proof in a process that has no execution session.

    expected_statement_hash represents an optional out-of-band trust anchor.
    A production deployment can obtain that digest from a signed DSSE/Sigstore
    envelope, KMS signature, transparency log, or another trusted channel.
    """

    proof = IndependentExecutionProof.from_mapping(value)
    if (
        expected_statement_hash is not None
        and proof.statement_hash != expected_statement_hash
    ):
        raise ProtocolViolation(
            "independent execution proof trusted digest mismatch"
        )
    return proof
