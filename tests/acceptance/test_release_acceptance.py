from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.golden_run import RecoveryGoldenRun, run_recovery_golden_slice

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
        placement={"target": "cell-b", "migrationStrategy": "drain-rebind"},
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


def reconciler(*, failing_workflow: bool = False) -> ApplyReconciler:
    return ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(failing_workflow=failing_workflow),
    )


def test_acceptance_happy_path_promotes_with_release_evidence() -> None:
    result = reconciler().reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
        observed_placement="cell-b",
    )

    assert result.phase == "promoted"
    assert result.error is None
    assert result.evidence["kind"] == "ReleaseEvidence"
    assert result.evidence["phase"] == "promoted"
    assert len(result.evidence["receipts"]) == 5
    assert result.evidence["rollback_receipts"] == []
    assert result.evidence["eval_results"][0]["passed"] is True


def test_acceptance_gate_failure_blocks_with_violation_evidence() -> None:
    result = reconciler().reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.70, "ece": 0.03},
    )

    assert result.phase == "blocked"
    assert result.evidence["phase"] == "blocked"
    violation = result.evidence["eval_results"][0]["violations"][0]
    assert violation["metric"] == "accuracy"
    assert violation["reason"] == "THRESHOLD_FAILED"
    assert result.evidence["rollback_receipts"] == []


def test_acceptance_apply_failure_rolls_back_only_completed_work() -> None:
    result = reconciler(failing_workflow=True).reconcile(build_plan())

    assert result.phase == "rolled_back"
    assert result.error == "temporal unavailable"
    assert [item["binding_name"] for item in result.evidence["receipts"]] == [
        "sandbox",
        "tools",
        "harness",
    ]
    assert [
        item["binding_name"] for item in result.evidence["rollback_receipts"]
    ] == ["harness", "tools", "sandbox"]
    assert result.evidence["apply_error"] == "temporal unavailable"


def test_acceptance_recovery_identity_loss_rolls_back_with_evidence() -> None:
    result = run_recovery_golden_slice(
        reconciler(),
        build_plan(),
        RecoveryGoldenRun(
            observed_placement="cell-a",
            recovery_evidence={
                "success": True,
                "identityPreserved": False,
                "sourceSandboxRef": "sandbox://cell-a/old",
                "targetSandboxRef": "sandbox://cell-b/new",
                "snapshotRef": "snapshot://old",
            },
        ),
    )

    assert result.phase == "rolled_back"
    assert result.evidence["recovery"]["identityPreserved"] is False
    assert result.evidence["placement"]["migrating"] is True
    violations = result.evidence["eval_results"][0]["violations"]
    assert any(
        item["metric"] == "recovery_identity_preserved"
        and item["reason"] == "THRESHOLD_FAILED"
        for item in violations
    )
    assert len(result.evidence["rollback_receipts"]) == 5
