import pytest

from agent_control_plane.decision_adapter import DecisionGatewayAdapter
from agent_control_plane.example_adapters import (
    CodexHarnessAdapter,
    KubernetesSandboxAdapter,
    TemporalWorkflowAdapter,
)
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.reconciler import ReleaseReconciler
from agent_control_plane.registry import ProviderRegistry
from agent_control_plane.tool_adapter import MCPToolAdapter


def make_plan():
    return ResolvedReleasePlan(
        release_name="sre-v1",
        bundle_name="sre",
        version="v1",
        bindings={
            "decision": {
                "metadata": {"name": "decision"},
                "spec": {
                    "type": "decision",
                    "provider": "decision-gateway",
                    "endpointRef": "service://decision",
                    "config": {
                        "decisionTypes": ["mcp_tool_router"],
                        "fallbackBinding": "harness",
                    },
                },
            },
            "harness": {
                "metadata": {"name": "harness"},
                "spec": {"type": "harness", "provider": "codex"},
            },
            "workflow": {
                "metadata": {"name": "workflow"},
                "spec": {"type": "workflow", "provider": "temporal"},
            },
            "tool": {
                "metadata": {"name": "tool"},
                "spec": {
                    "type": "tool",
                    "provider": "mcp",
                    "config": {
                        "mode": "read-only",
                        "capabilities": ["metrics.read"],
                    },
                },
            },
            "sandbox": {
                "metadata": {"name": "sandbox"},
                "spec": {"type": "sandbox", "provider": "k8s-agent-sandbox"},
            },
        },
        eval_gates=("smoke",),
    )


def registry():
    result = ProviderRegistry()
    result.register(DecisionGatewayAdapter())
    result.register(CodexHarnessAdapter())
    result.register(TemporalWorkflowAdapter())
    result.register(MCPToolAdapter())
    result.register(KubernetesSandboxAdapter())
    return result


def test_dry_run_reconciles_to_promoted() -> None:
    result = ReleaseReconciler(registry()).reconcile(make_plan(), dry_run=True)

    assert result.phase == "promoted"
    assert result.evidence["dry_run"] is True
    assert set(result.prepared) == {
        "decision",
        "harness",
        "workflow",
        "tool",
        "sandbox",
    }


def test_apply_mode_is_explicitly_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        ReleaseReconciler(registry()).reconcile(make_plan(), dry_run=False)
