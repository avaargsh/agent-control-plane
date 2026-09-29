from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .golden_slice_replay import canonical_replay_digest, freeze_json_mapping


class FrozenEvidenceViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class FrozenEvidence:
    snapshot: Mapping[str, Any]
    digest: str

    @classmethod
    def capture(cls, evidence: Mapping[str, Any]) -> "FrozenEvidence":
        snapshot = freeze_json_mapping(evidence)
        return cls(
            snapshot=snapshot,
            digest=canonical_replay_digest(snapshot),
        )

    def verify(self) -> None:
        actual = canonical_replay_digest(self.snapshot)
        if actual != self.digest:
            raise FrozenEvidenceViolation(
                f"frozen evidence digest changed: expected {self.digest}, got {actual}"
            )


def resume_from_frozen_evidence(
    frozen: FrozenEvidence,
    *,
    continuation: Callable[[Mapping[str, Any]], Any],
    live_reader: Callable[[], Mapping[str, Any]] | None = None,
) -> Any:
    """Resume exclusively from the approved snapshot.

    live_reader is accepted only as an explicit tripwire: if supplied, resume
    fails before it can be called. This makes the no-live-reread boundary
    executable and testable.
    """
    frozen.verify()

    if live_reader is not None:
        raise FrozenEvidenceViolation(
            "live evidence reader is forbidden after approval"
        )

    return continuation(frozen.snapshot)
