from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .plan import ResolvedReleasePlan


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    policy_refs: tuple[str, ...]
    reasons: tuple[str, ...] = ()


class ReleasePolicyEngine(Protocol):
    def evaluate(
        self,
        plan: ResolvedReleasePlan,
    ) -> PolicyDecision:
        ...


@dataclass(frozen=True)
class MappingPolicyEngine:
    """Deterministic reference engine.

    Every referenced policy must exist in the mapping.
    Unknown policies fail closed.
    """

    decisions: Mapping[str, bool]

    def evaluate(
        self,
        plan: ResolvedReleasePlan,
    ) -> PolicyDecision:
        reasons: list[str] = []
        allowed = True

        for ref in plan.policy_refs:
            decision = self.decisions.get(ref)

            if decision is None:
                allowed = False
                reasons.append(
                    f"UNKNOWN_POLICY:{ref}"
                )
                continue

            if not decision:
                allowed = False
                reasons.append(
                    f"POLICY_DENIED:{ref}"
                )

        return PolicyDecision(
            allowed=allowed,
            policy_refs=tuple(
                plan.policy_refs
            ),
            reasons=tuple(reasons),
        )
