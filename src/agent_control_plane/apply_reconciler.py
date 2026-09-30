from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .authority import (
    AuthorityAdmissionDecision,
    admit_authority_change,
    authority_digest,
)
from .dependency_graph import dependency_order
from .decision_eval import validate_decision_eval_artifact
from .golden_slice_replay import freeze_json_mapping
from .frozen_evidence import FrozenEvidence, resume_from_frozen_evidence
from .eval_engine import EvalResult, evaluate_gate
from .executors import (
    ExecutionReceipt,
    ExecutorRegistry,
    RollbackReceipt,
)
from .plan import ResolvedReleasePlan
from .placement import placement_evidence
from .policy_engine import (
    PolicyDecision,
    ReleasePolicyEngine,
)
from .registry import ProviderRegistry
from .recovery import normalize_recovery_evidence
from .release_evidence import seal_release_evidence
from .state_machine import ReleasePhase, ReleaseState


@dataclass(frozen=True)
class ApplyResult:
    release_name: str
    phase: str
    receipts: tuple[ExecutionReceipt, ...]
    rollback_receipts: tuple[RollbackReceipt, ...]
    eval_results: tuple[EvalResult, ...]
    evidence: Mapping[str, Any]
    policy_decision: PolicyDecision | None = None
    authority_decision: AuthorityAdmissionDecision | None = None
    error: str | None = None


class ApplyReconciler:
    """Apply a resolved release through policy and dependency-ordered executors."""

    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        executors: ExecutorRegistry,
        policy_engine: ReleasePolicyEngine | None = None,
    ) -> None:
        self.providers = providers
        self.executors = executors
        self.policy_engine = policy_engine

    def _rollback(
        self,
        *,
        plan: ResolvedReleasePlan,
        applied: list[
            tuple[
                dict[str, Any],
                ExecutionReceipt,
            ]
        ],
    ) -> list[RollbackReceipt]:
        rollback_receipts: list[
            RollbackReceipt
        ] = []

        for binding, receipt in reversed(
            applied
        ):
            spec = binding["spec"]
            executor = self.executors.get(
                spec["type"],
                spec["provider"],
            )
            rollback_receipts.append(
                executor.rollback(
                    plan=plan,
                    binding=binding,
                    receipt=receipt,
                )
            )

        return rollback_receipts

    def _evaluate_policy(
        self,
        plan: ResolvedReleasePlan,
    ) -> PolicyDecision:
        if not plan.policy_refs:
            return PolicyDecision(
                allowed=True,
                policy_refs=(),
            )

        if self.policy_engine is None:
            return PolicyDecision(
                allowed=False,
                policy_refs=tuple(
                    plan.policy_refs
                ),
                reasons=(
                    "POLICY_ENGINE_REQUIRED",
                ),
            )

        return self.policy_engine.evaluate(
            plan
        )

    def reconcile(
        self,
        plan: ResolvedReleasePlan,
        *,
        eval_gates: Sequence[
            Mapping[str, Any]
        ] = (),
        metrics: Mapping[
            str,
            float,
        ] | None = None,
        observed_placement: str | None = None,
        recovery_evidence: Mapping[str, Any] | None = None,
        decision_eval_artifact: Mapping[str, Any] | None = None,
        golden_slice: Mapping[str, Any] | None = None,
        approved_evidence: FrozenEvidence | None = None,
        deployed_authority: Mapping[str, Any] | None = None,
        proposed_authority: Mapping[str, Any] | None = None,
        initial_authority_approved: bool = False,
    ) -> ApplyResult:
        if approved_evidence is not None:
            golden_slice_provenance = resume_from_frozen_evidence(
                approved_evidence,
                continuation=freeze_json_mapping,
            )
        else:
            golden_slice_provenance = freeze_json_mapping(golden_slice or {})
        placement = placement_evidence(
            plan.placement,
            observed_target=observed_placement,
        )
        recovery, recovery_metrics = normalize_recovery_evidence(
            recovery_evidence
        )
        state = ReleaseState(
            plan.release_name
        )
        state.transition(
            ReleasePhase.VALIDATED
        )
        state.transition(
            ReleasePhase.RESOLVED
        )

        decision_eval_evidence: Mapping[str, Any] | None = None
        decision_eval_metrics: Mapping[str, float] = {}
        if decision_eval_artifact is not None:
            decision_eval_evidence = freeze_json_mapping(
                decision_eval_artifact
            )
            decision_validation = validate_decision_eval_artifact(
                decision_eval_evidence
            )
            if not decision_validation.valid:
                state.transition(ReleasePhase.BLOCKED)
                decision_error = (
                    "DECISION_EVAL_INVALID:"
                    + str(decision_validation.reason)
                )
                evidence = seal_release_evidence({
                    "kind": "ReleaseEvidence",
                    "release": plan.release_name,
                    "golden_slice": golden_slice_provenance,
                    "placement": placement,
                    "recovery": recovery,
                    "decision_eval": decision_eval_evidence,
                    "receipts": [],
                    "rollback_receipts": [],
                    "eval_results": [],
                    "phase": state.phase.value,
                    "decision_eval_error": decision_error,
                })
                return ApplyResult(
                    release_name=plan.release_name,
                    phase=state.phase.value,
                    receipts=(),
                    rollback_receipts=(),
                    eval_results=(),
                    evidence=evidence,
                    error=decision_error,
                )
            decision_eval_metrics = decision_validation.metrics

        authority_decision: AuthorityAdmissionDecision | None = None
        authority_error: str | None = None
        if plan.authority_ref is not None or plan.authority_digest is not None:
            if proposed_authority is None:
                authority_error = "AUTHORITY_ENVELOPE_REQUIRED"
            elif proposed_authority.get("metadata", {}).get("name") != plan.authority_ref:
                authority_error = "AUTHORITY_REF_MISMATCH"
            elif proposed_authority.get("spec", {}).get("releaseRef") != plan.release_name:
                authority_error = "AUTHORITY_RELEASE_MISMATCH"
            elif authority_digest(proposed_authority) != plan.authority_digest:
                authority_error = "AUTHORITY_DIGEST_MISMATCH"
            else:
                authority_decision = admit_authority_change(
                    deployed_authority,
                    proposed_authority,
                    allow_initial=(
                        initial_authority_approved
                    ),
                )
                if not authority_decision.admitted:
                    authority_error = (
                        "AUTHORITY_ADMISSION_DENIED: "
                        + ",".join(authority_decision.reasons)
                    )

        authority_evidence = (
            asdict(authority_decision)
            if authority_decision is not None
            else None
        )

        if authority_error is not None:
            state.transition(ReleasePhase.BLOCKED)
            evidence = seal_release_evidence({
                "kind": "ReleaseEvidence",
                "release": plan.release_name,
                "golden_slice": golden_slice_provenance,
                "placement": placement,
                "recovery": recovery,
                "decision_eval": decision_eval_evidence,
                "authority": authority_evidence,
                "receipts": [],
                "rollback_receipts": [],
                "eval_results": [],
                "phase": state.phase.value,
                "authority_error": authority_error,
            })
            return ApplyResult(
                release_name=plan.release_name,
                phase=state.phase.value,
                receipts=(),
                rollback_receipts=(),
                eval_results=(),
                evidence=evidence,
                authority_decision=authority_decision,
                error=authority_error,
            )

        policy_decision = (
            self._evaluate_policy(plan)
        )

        if not policy_decision.allowed:
            state.transition(
                ReleasePhase.BLOCKED
            )
            evidence = seal_release_evidence({
                "kind": "ReleaseEvidence",
                "release": plan.release_name,
                "golden_slice": golden_slice_provenance,
                "placement": placement,
                "recovery": recovery,
                "decision_eval": decision_eval_evidence,
                "authority": authority_evidence,
                "policy": asdict(
                    policy_decision
                ),
                "receipts": [],
                "rollback_receipts": [],
                "eval_results": [],
                "phase": state.phase.value,
            })
            return ApplyResult(
                release_name=plan.release_name,
                phase=state.phase.value,
                receipts=(),
                rollback_receipts=(),
                eval_results=(),
                evidence=evidence,
                policy_decision=policy_decision,
                authority_decision=authority_decision,
            )

        conformance = self.providers.conformance(plan)
        incompatible = {
            name: result
            for name, result in conformance.items()
            if not result.compatible
        }
        if incompatible:
            state.transition(ReleasePhase.BLOCKED)
            evidence = seal_release_evidence({
                "kind": "ReleaseEvidence",
                "release": plan.release_name,
                "golden_slice": golden_slice_provenance,
                "placement": placement,
                "recovery": recovery,
                "decision_eval": decision_eval_evidence,
                "authority": authority_evidence,
                "policy": asdict(policy_decision),
                "conformance": {
                    name: asdict(result)
                    for name, result in conformance.items()
                },
                "receipts": [],
                "rollback_receipts": [],
                "eval_results": [],
                "phase": state.phase.value,
            })
            missing = "; ".join(
                f"{name}: {','.join(result.missing)}"
                for name, result in incompatible.items()
            )
            return ApplyResult(
                release_name=plan.release_name,
                phase=state.phase.value,
                receipts=(),
                rollback_receipts=(),
                eval_results=(),
                evidence=evidence,
                policy_decision=policy_decision,
                authority_decision=authority_decision,
                error=f"provider feature mismatch: {missing}",
            )

        prepared = self.providers.prepare(
            plan
        )
        order = dependency_order(
            plan.bindings
        )

        state.transition(
            ReleasePhase.DEPLOYING
        )

        receipts: list[
            ExecutionReceipt
        ] = []
        applied: list[
            tuple[
                dict[str, Any],
                ExecutionReceipt,
            ]
        ] = []

        try:
            for binding_name in order:
                binding = plan.bindings[
                    binding_name
                ]
                spec = binding["spec"]
                executor = (
                    self.executors.get(
                        spec["type"],
                        spec["provider"],
                    )
                )
                receipt = executor.apply(
                    plan=plan,
                    binding=binding,
                    prepared=prepared[
                        binding_name
                    ],
                )
                receipts.append(
                    receipt
                )
                applied.append(
                    (
                        binding,
                        receipt,
                    )
                )
        except Exception as exc:
            rollback_receipts = (
                self._rollback(
                    plan=plan,
                    applied=applied,
                )
            )
            state.transition(
                ReleasePhase.ROLLED_BACK
            )

            evidence = seal_release_evidence({
                "kind": "ReleaseEvidence",
                "release": plan.release_name,
                "golden_slice": golden_slice_provenance,
                "placement": placement,
                "recovery": recovery,
                "decision_eval": decision_eval_evidence,
                "authority": authority_evidence,
                "policy": asdict(
                    policy_decision
                ),
                "dependency_order": order,
                "prepared": prepared,
                "receipts": [
                    asdict(receipt)
                    for receipt in receipts
                ],
                "rollback_receipts": [
                    asdict(receipt)
                    for receipt in (
                        rollback_receipts
                    )
                ],
                "eval_results": [],
                "phase": state.phase.value,
                "apply_error": str(exc),
            })

            return ApplyResult(
                release_name=plan.release_name,
                phase=state.phase.value,
                receipts=tuple(
                    receipts
                ),
                rollback_receipts=tuple(
                    rollback_receipts
                ),
                eval_results=(),
                evidence=evidence,
                policy_decision=policy_decision,
                authority_decision=authority_decision,
                error=str(exc),
            )

        state.transition(
            ReleasePhase.EVALUATING
        )

        eval_results = tuple(
            evaluate_gate(
                gate,
                {
                    **(metrics or {}),
                    **decision_eval_metrics,
                    **recovery_metrics,
                },
            )
            for gate in eval_gates
        )

        failed = [
            (
                gate,
                result,
            )
            for gate, result in zip(
                eval_gates,
                eval_results,
                strict=True,
            )
            if not result.passed
        ]

        rollback_receipts: list[
            RollbackReceipt
        ] = []

        if not failed:
            state.transition(
                ReleasePhase.PROMOTED
            )
        else:
            actions = {
                gate.get(
                    "spec",
                    {},
                ).get(
                    "onFailure",
                    "block",
                )
                for gate, _ in failed
            }

            if "rollback" in actions:
                rollback_receipts = (
                    self._rollback(
                        plan=plan,
                        applied=applied,
                    )
                )
                state.transition(
                    ReleasePhase.ROLLED_BACK
                )
            else:
                state.transition(
                    ReleasePhase.BLOCKED
                )

        evidence = seal_release_evidence({
            "kind": "ReleaseEvidence",
            "release": plan.release_name,
            "golden_slice": golden_slice_provenance,
            "placement": placement,
            "recovery": recovery,
            "decision_eval": decision_eval_evidence,
            "authority": authority_evidence,
            "policy": asdict(
                policy_decision
            ),
            "dependency_order": order,
            "prepared": prepared,
            "receipts": [
                asdict(receipt)
                for receipt in receipts
            ],
            "rollback_receipts": [
                asdict(receipt)
                for receipt in (
                    rollback_receipts
                )
            ],
            "eval_results": [
                {
                    "passed": result.passed,
                    "violations": [
                        asdict(
                            violation
                        )
                        for violation in (
                            result.violations
                        )
                    ],
                    "on_failure": gate.get(
                        "spec",
                        {},
                    ).get(
                        "onFailure",
                        "block",
                    ),
                }
                for gate, result in zip(
                    eval_gates,
                    eval_results,
                    strict=True,
                )
            ],
            "phase": state.phase.value,
        })

        return ApplyResult(
            release_name=plan.release_name,
            phase=state.phase.value,
            receipts=tuple(
                receipts
            ),
            rollback_receipts=tuple(
                rollback_receipts
            ),
            eval_results=eval_results,
            evidence=evidence,
            policy_decision=policy_decision,
            authority_decision=authority_decision,
        )
