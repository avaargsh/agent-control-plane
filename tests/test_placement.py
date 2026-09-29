import pytest

from agent_control_plane.placement import (
    placement_evidence,
    resolve_placement_transition,
)


def test_same_target_is_stable() -> None:
    result = resolve_placement_transition(
        {"target": "cell-a"},
        observed_target="cell-a",
    )
    assert result.migrating is False
    assert result.source == "cell-a"
    assert result.target == "cell-a"


def test_target_change_is_explicit_migration() -> None:
    result = resolve_placement_transition(
        {
            "target": "cell-b",
            "migrationStrategy": "drain-rebind",
        },
        observed_target="cell-a",
    )
    assert result.migrating is True
    assert result.strategy == "drain-rebind"


def test_first_placement_is_not_migration() -> None:
    result = resolve_placement_transition({"target": "cell-a"})
    assert result.migrating is False


def test_placement_evidence_is_audit_friendly() -> None:
    assert placement_evidence(
        {"target": "cell-b"},
        observed_target="cell-a",
    ) == {
        "source": "cell-a",
        "target": "cell-b",
        "migrating": True,
        "strategy": "rebind",
    }


@pytest.mark.parametrize(
    "placement",
    [
        {"target": ""},
        {"target": 3},
        {"target": "cell-a", "migrationStrategy": "live-copy"},
    ],
)
def test_invalid_placement_fails_closed(placement) -> None:
    with pytest.raises(ValueError):
        resolve_placement_transition(placement)
