from __future__ import annotations

from typing import Any


class BindingResolutionError(ValueError):
    pass


def resolve_bindings(
    release: dict[str, Any],
    bindings: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Resolve release binding names to validated binding documents."""
    by_name: dict[str, dict[str, Any]] = {}

    for binding in bindings:
        name = binding.get("metadata", {}).get("name")
        if not name:
            raise BindingResolutionError("binding is missing metadata.name")
        if name in by_name:
            raise BindingResolutionError(f"duplicate binding: {name}")
        by_name[name] = binding

    requested = release.get("spec", {}).get("bindings", [])
    resolved: dict[str, dict[str, Any]] = {}

    for name in requested:
        if name not in by_name:
            raise BindingResolutionError(f"release references unknown binding: {name}")
        resolved[name] = by_name[name]

    return resolved
