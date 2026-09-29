from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.decision_adapter import DecisionGatewayAdapter
from agent_control_plane.executors import (
    ExecutorRegistry,
    InMemoryExecutor,
)
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
                "spec": {
                    "type": "sandbox",
                    "provider": "k8s-agent-sandbox",
                },
            },
            "tools": {
                "metadata": {"name": "tools"},
                "spec": {
                    "type": "tool",
                    "provider": "mcp",
                    "dependsOn": ["sandbox"],
                },
            },
            "harness": {
                "metadata": {"name": "harness"},
                "spec": {
                    "type": "harness",
                    "provider": "codex",
                    "dependsOn": ["tools"],
                },
            },
            "workflow": {
                "metadata": {"name": "workflow"},
                "spec": {
                    "type": "workflow",
                    "provider": "temporal",
                    "dependsOn": ["harness"],
                },
            },
            "decision": {
                "metadata": {"name": "decision"},
                "spec": {
                    "type": "decision",
                    "provider": "decision-gateway",
                    "dependsOn": ["workflow"],
                },
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


def executor_registry(
    *,
    failing_workflow: bool = False,
) -> ExecutorRegistry:
    registry = ExecutorRegistry()

    registry.register(
        InMemoryExecutor("sandbox", "k8s-agent-sandbox")
    )
    registry.register(InMemoryExecutor("tool", "mcp"))
    registry.register(InMemoryExecutor("harness", "codex"))

    if failing_workflow:
        class FailingWorkflowExecutor(InMemoryExecutor):
            def apply(self, **kwargs):
                raise RuntimeError("temporal unavailable")

        registry.register(
            FailingWorkflowExecutor("workflow", "temporal")
        )
    else:
        registry.register(
            InMemoryExecutor("workflow", "temporal")
        )

    registry.register(
        InMemoryExecutor("decision", "decision-gateway")
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
            {
                "metric": "false_automation_rate",
                "op": "lte",
                "value": 0.01,
            },
        ],
        "onFailure": "rollback",
    }
}


def test_apply_promotes_in_dependency_order() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
    )

    assert result.phase == "promoted"
    assert [
        receipt.binding_name
        for receipt in result.receipts
    ] == [
        "sandbox",
        "tools",
        "harness",
        "workflow",
        "decision",
    ]
    assert result.rollback_receipts == ()
    assert result.eval_results[0].passed is True


def test_apply_is_idempotent_on_retry() -> None:
    executors = executor_registry()
    reconciler = ApplyReconciler(
        providers=provider_registry(),
        executors=executors,
    )

    first = reconciler.reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
    )
    second = reconciler.reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
    )

    assert all(item.changed for item in first.receipts)
    assert all(not item.changed for item in second.receipts)


def test_apply_blocks_failed_gate_without_compensation() -> None:
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


def test_failed_rollback_gate_compensates_reverse_apply_order() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[ROLLBACK_GATE],
        metrics={"false_automation_rate": 0.20},
    )

    assert result.phase == "rolled_back"
    assert [
        item.binding_name
        for item in result.rollback_receipts
    ] == [
        "decision",
        "workflow",
        "harness",
        "tools",
        "sandbox",
    ]
    assert all(item.rolled_back for item in result.rollback_receipts)


def test_partial_apply_failure_compensates_completed_bindings() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(
            failing_workflow=True,
        ),
    ).reconcile(
        build_plan(),
    )

    assert result.phase == "rolled_back"
    assert result.error == "temporal unavailable"
    assert [
        item.binding_name
        for item in result.receipts
    ] == [
        "sandbox",
        "tools",
        "harness",
    ]
    assert [
        item.binding_name
        for item in result.rollback_receipts
    ] == [
        "harness",
        "tools",
        "sandbox",
    ]


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


def test_capability_mismatch_blocks_before_any_mutation() -> None:
    plan = build_plan()
    plan.bindings["workflow"]["spec"]["capabilities"] = ["durable", "signal"]

    class CountingExecutor(InMemoryExecutor):
        calls = 0

        def apply(self, **kwargs):
            type(self).calls += 1
            return super().apply(**kwargs)

    executors = ExecutorRegistry()
    for provider_type, provider_name in [
        ("sandbox", "k8s-agent-sandbox"),
        ("tool", "mcp"),
        ("harness", "codex"),
        ("workflow", "temporal"),
        ("decision", "decision-gateway"),
    ]:
        executors.register(CountingExecutor(provider_type, provider_name))

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executors,
    ).reconcile(plan)

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.rollback_receipts == ()
    assert CountingExecutor.calls == 0
    assert "workflow: durable,signal" in result.error
    assert result.evidence["conformance"]["workflow"]["compatible"] is False


def test_capability_mismatch_blocks_before_any_mutation() -> None:
    plan = build_plan()
    plan.bindings["workflow"]["spec"]["capabilities"] = ["durable", "signal"]

    class CountingExecutor(InMemoryExecutor):
        calls = 0

        def apply(self, **kwargs):
            type(self).calls += 1
            return super().apply(**kwargs)

    executors = ExecutorRegistry()
    for provider_type, provider_name in [
        ("sandbox", "k8s-agent-sandbox"),
        ("tool", "mcp"),
        ("harness", "codex"),
        ("workflow", "temporal"),
        ("decision", "decision-gateway"),
    ]:
        executors.register(CountingExecutor(provider_type, provider_name))

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executors,
    ).reconcile(plan)

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.rollback_receipts == ()
    assert CountingExecutor.calls == 0
    assert "workflow: durable,signal" in result.error
    assert result.evidence["conformance"]["workflow"]["compatible"] is False
