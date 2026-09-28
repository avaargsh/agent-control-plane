import pytest

from agent_control_plane.dependency_graph import (
    DependencyGraphError,
    dependency_order,
)


def binding(*depends_on):
    return {
        "spec": {
            "type": "tool",
            "provider": "demo",
            "dependsOn": list(depends_on),
        }
    }


def test_dependency_order_is_topological() -> None:
    bindings = {
        "sandbox": binding(),
        "tools": binding("sandbox"),
        "harness": binding("tools"),
        "workflow": binding("harness"),
        "decision": binding("harness"),
        "traffic": binding("workflow", "decision"),
    }

    order = dependency_order(bindings)

    assert order.index("sandbox") < order.index("tools")
    assert order.index("tools") < order.index("harness")
    assert order.index("harness") < order.index("workflow")
    assert order.index("harness") < order.index("decision")
    assert order.index("workflow") < order.index("traffic")
    assert order.index("decision") < order.index("traffic")


def test_unknown_dependency_fails() -> None:
    with pytest.raises(DependencyGraphError):
        dependency_order(
            {
                "harness": binding("missing"),
            }
        )


def test_cycle_fails() -> None:
    with pytest.raises(DependencyGraphError):
        dependency_order(
            {
                "a": binding("b"),
                "b": binding("a"),
            }
        )
