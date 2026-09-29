from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PlacementTransition:
    """Normalized placement transition for a resolved release.

    Placement is control-plane intent. Runtime and sandbox providers consume the
    resulting target through bindings; they do not own placement policy.
    """

    source: str | None
    target: str | None
    migrating: bool
    strategy: str


def resolve_placement_transition(
    placement: Mapping[str, Any] | None,
    *,
    observed_target: str | None = None,
) -> PlacementTransition:
    desired = dict(placement or {})
    target = desired.get("target")
    strategy = desired.get("migrationStrategy", "rebind")

    if target is not None and (not isinstance(target, str) or not target.strip()):
        raise ValueError("placement.target must be a non-empty string")
    if strategy not in {"rebind", "drain-rebind"}:
        raise ValueError("unsupported placement migration strategy")

    return PlacementTransition(
        source=observed_target,
        target=target,
        migrating=(
            observed_target is not None
            and target is not None
            and observed_target != target
        ),
        strategy=strategy,
    )


def placement_evidence(
    placement: Mapping[str, Any] | None,
    *,
    observed_target: str | None = None,
) -> dict[str, Any]:
    transition = resolve_placement_transition(
        placement,
        observed_target=observed_target,
    )
    return {
        "source": transition.source,
        "target": transition.target,
        "migrating": transition.migrating,
        "strategy": transition.strategy,
    }
