from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReleasePhase(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    RESOLVED = "resolved"
    DEPLOYING = "deploying"
    EVALUATING = "evaluating"
    PROMOTED = "promoted"
    BLOCKED = "blocked"
    ROLLED_BACK = "rolled_back"


_ALLOWED: dict[ReleasePhase, set[ReleasePhase]] = {
    ReleasePhase.DRAFT: {ReleasePhase.VALIDATED, ReleasePhase.BLOCKED},
    ReleasePhase.VALIDATED: {ReleasePhase.RESOLVED, ReleasePhase.BLOCKED},
    ReleasePhase.RESOLVED: {ReleasePhase.DEPLOYING, ReleasePhase.BLOCKED},
    ReleasePhase.DEPLOYING: {
        ReleasePhase.EVALUATING,
        ReleasePhase.BLOCKED,
        ReleasePhase.ROLLED_BACK,
    },
    ReleasePhase.EVALUATING: {
        ReleasePhase.PROMOTED,
        ReleasePhase.BLOCKED,
        ReleasePhase.ROLLED_BACK,
    },
    ReleasePhase.PROMOTED: {ReleasePhase.ROLLED_BACK},
    ReleasePhase.BLOCKED: set(),
    ReleasePhase.ROLLED_BACK: set(),
}


@dataclass
class ReleaseState:
    release_name: str
    phase: ReleasePhase = ReleasePhase.DRAFT

    def transition(self, target: ReleasePhase) -> None:
        allowed = _ALLOWED[self.phase]
        if target not in allowed:
            raise ValueError(
                f"invalid release transition: {self.phase.value} -> {target.value}"
            )
        self.phase = target
