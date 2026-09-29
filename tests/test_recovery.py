import pytest

from agent_control_plane.recovery import normalize_recovery_evidence


def test_recovery_metrics_are_eval_ready() -> None:
    evidence, metrics = normalize_recovery_evidence(
        {"success": True, "identityPreserved": False}
    )
    assert evidence["success"] is True
    assert metrics == {
        "recovery_success": 1.0,
        "recovery_identity_preserved": 0.0,
    }


@pytest.mark.parametrize(
    "evidence",
    [
        {},
        {"success": True},
        {"success": 1, "identityPreserved": True},
    ],
)
def test_invalid_recovery_evidence_fails_closed(evidence) -> None:
    with pytest.raises(ValueError):
        normalize_recovery_evidence(evidence)
