import pytest

from agent_control_plane.frozen_evidence import (
    FrozenEvidence,
    FrozenEvidenceViolation,
    resume_from_frozen_evidence,
)


def test_resume_consumes_only_approved_snapshot():
    live = {
        "alert": "gpu-xid",
        "severity": "critical",
        "node": "gpu-07",
    }
    frozen = FrozenEvidence.capture(live)

    live["severity"] = "warning"

    resumed = resume_from_frozen_evidence(
        frozen,
        continuation=lambda evidence: dict(evidence),
    )

    assert resumed["severity"] == "critical"


def test_resume_rejects_live_reader_without_calling_it():
    frozen = FrozenEvidence.capture({"alert": "gpu-xid"})
    calls = {"count": 0}

    def live_reader():
        calls["count"] += 1
        return {"alert": "changed"}

    with pytest.raises(
        FrozenEvidenceViolation,
        match="live evidence reader is forbidden",
    ):
        resume_from_frozen_evidence(
            frozen,
            continuation=lambda evidence: evidence,
            live_reader=live_reader,
        )

    assert calls["count"] == 0


def test_resume_detects_tampered_frozen_snapshot():
    frozen = FrozenEvidence.capture({"alert": "gpu-xid"})
    object.__setattr__(frozen, "snapshot", {"alert": "different"})

    with pytest.raises(
        FrozenEvidenceViolation,
        match="digest changed",
    ):
        resume_from_frozen_evidence(
            frozen,
            continuation=lambda evidence: evidence,
        )



def test_resume_continuation_cannot_mutate_frozen_snapshot():
    frozen = FrozenEvidence.capture({
        "alert": "gpu-xid",
        "refs": {"bundle": "evidence://approved/001"},
    })

    def mutate(evidence):
        evidence["alert"] = "changed"
        evidence["refs"]["bundle"] = "evidence://mutated"
        return evidence

    resumed = resume_from_frozen_evidence(
        frozen,
        continuation=mutate,
    )

    assert resumed["alert"] == "changed"
    assert frozen.snapshot["alert"] == "gpu-xid"
    assert frozen.snapshot["refs"]["bundle"] == "evidence://approved/001"
    frozen.verify()
