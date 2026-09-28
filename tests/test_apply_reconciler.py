from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.decision_adapter import DecisionGatewayAdapter
from agent_control_plane.executors import ExecutorRegistry, InMemoryExecutor
from agent_control_plane.example_adapters import (
    CodexHarnessAdapter,
    KubernetesSandboxAdapter,
    TemporalWorkflowAdapter,
)
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.registry import ProviderRegistry
from agent_control_plane.tool_adapter import MCPToolAdapter


def build_plan() -> ResolvedReleasePlan:
    return ResolvedReleasePlan(
        release_name="sre-v1",
        bundle_name="sre",
        version="v1",
        bindings={
            "sandbox": {
                "metadata": {"name": "sandbox"},
                "spec": {"type": "sandbox", "provider": "k8s-agent-sandbox"},
            },
            "tools": {
                "metadata": {"name": "tools"},
                "spec": {"type": "tool", "provider": "mcp"},
            },
            "harness": {
                "metadata": {"name": "harness"},
                "spec": {"type": "harness", "provider": "codex"},
            },
            "workflow": {
                "metadata": {"name": "workflow"},
                "spec": {"type": "workflow", "provider": "temporal"},
            },
            "decision": {
                "metadata": {"name": "decision"},
                "spec": {"type": "decision", "provider": "decision-gateway"},
            },
        },
    )


def provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(KubernetesSandboxAdapter())
    registry.register(MCPToolAdapter())
    registry.register(CodexHarnessAdapter())
    registry.register(TemporalWorkflowAdapter())
    registry.register(DecisionGatewayAdapter())
    return registry


def executor_registry() -> ExecutorRegistry:
    registry = ExecutorRegistry()
    for provider_type, provider_name in [
        ("sandbox", "k8s-agent-sandbox"),
        ("tool", "mcp"),
        ("harness", "codex"),
        ("workflow", "temporal"),
        ("decision", "decision-gateway"),
    ]:
        registry.register(
            InMemoryExecutor(provider_type, provider_name)
        )
    return registry


PASS_GATE = {
    "spec": {
        "conditions": [
            {"metric": "accuracy", "op": "gte", "value": 0.9},
            {"metric": "ece", "op": "lte", "value": 0.05},
        ],
        "onFailure": "block",
    }
}

ROLLBACK_GATE = {
    "spec": {
        "conditions": [
            {"metric": "false_automation_rate", "op": "lte", "value": 0.01},
        ],
        "onFailure": "rollback",
    }
}


def test_apply_promotes_only_after_gate_passes() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
    )

    assert result.phase == "promoted"
    assert len(result.receipts) == 5
    assert result.rollback_receipts == ()
    assert result.eval_results[0].passed is True


def test_apply_blocks_failed_gate() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.70, "ece": 0.03},
    )

    assert result.phase == "blocked"
    assert result.rollback_receipts == ()
    assert result.eval_results[0].passed is False


def test_failed_rollback_gate_compensates_all_bindings() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[ROLLBACK_GATE],
        metrics={"false_automation_rate": 0.20},
    )

    assert result.phase == "rolled_back"
    assert len(result.rollback_receipts) == 5

    # Applied order ends with decision, so compensation starts there.
    assert result.rollback_receipts[0].binding_name == "decision"
    assert result.rollback_receipts[-1].binding_name == "sandbox"
    assert all(item.rolled_back for item in result.rollback_receipts)


def test_missing_metric_fails_closed() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95},
    )

    assert result.phase == "blocked"
