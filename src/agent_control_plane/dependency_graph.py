from __future__ import annotations

from collections import deque
from typing import Any, Mapping


class DependencyGraphError(ValueError):
    pass


def dependency_order(
    bindings: Mapping[str, dict[str, Any]],
) -> list[str]:
    names = set(bindings)
    indegree = {name: 0 for name in names}
    outgoing: dict[str, set[str]] = {
        name: set()
        for name in names
    }

    for name, binding in bindings.items():
        dependencies = list(
            binding.get("spec", {}).get("dependsOn", [])
        )

        if len(set(dependencies)) != len(dependencies):
            raise DependencyGraphError(
                f"binding {name!r} contains duplicate dependencies"
            )

        for dependency in dependencies:
            if dependency == name:
                raise DependencyGraphError(
                    f"binding {name!r} cannot depend on itself"
                )
            if dependency not in bindings:
                raise DependencyGraphError(
                    f"binding {name!r} depends on unknown binding "
                    f"{dependency!r}"
                )
            outgoing[dependency].add(name)
            indegree[name] += 1

    ready = deque(
        sorted(
            name
            for name, degree in indegree.items()
            if degree == 0
        )
    )
    ordered: list[str] = []

    while ready:
        current = ready.popleft()
        ordered.append(current)

        for dependent in sorted(outgoing[current]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)

    if len(ordered) != len(bindings):
        cyclic = sorted(
            name
            for name, degree in indegree.items()
            if degree > 0
        )
        raise DependencyGraphError(
            "binding dependency cycle detected: "
            + ", ".join(cyclic)
        )

    return ordered
