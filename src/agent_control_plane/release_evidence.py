from __future__ import annotations

from typing import Any, Mapping

from .golden_slice_replay import canonical_replay_digest, freeze_json_mapping


def seal_release_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = freeze_json_mapping(evidence)
    snapshot.pop("replay_digest", None)
    return {
        **snapshot,
        "replay_digest": canonical_replay_digest(snapshot),
    }


def verify_release_evidence(evidence: Mapping[str, Any]) -> bool:
    expected = evidence.get("replay_digest")
    snapshot = freeze_json_mapping(evidence)
    snapshot.pop("replay_digest", None)
    return (
        isinstance(expected, str)
        and expected == canonical_replay_digest(snapshot)
    )
