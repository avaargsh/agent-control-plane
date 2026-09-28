from agent_control_plane.decision_adapter import DecisionGatewayAdapter
from agent_control_plane.plan import ResolvedReleasePlan


def test_decision_adapter_prepares_binding() -> None:
    plan = ResolvedReleasePlan(
        release_name="agent-v1",
        bundle_name="agent",
        version="v1",
        bindings={},
    )
    binding = {
        "metadata": {"name": "decision-hot-path"},
        "spec": {
            "type": "decision",
            "provider": "decision-gateway",
            "endpointRef": "service://decision",
            "config": {
                "decisionTypes": ["mcp_tool_router", "severity"],
                "fallbackBinding": "harness-codex",
                "policyRef": "bounded-default",
            },
        },
    }

    prepared = DecisionGatewayAdapter().prepare(plan, binding)

    assert prepared["endpointRef"] == "service://decision"
    assert prepared["fallback_binding"] == "harness-codex"
    assert prepared["decision_types"] == ["mcp_tool_router", "severity"]
