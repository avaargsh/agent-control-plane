from agent_control_plane.runtime_clients import RuntimeApplyResult
from agent_control_plane.runtime_executors import (
    KubernetesSandboxExecutor,
    TemporalWorkflowExecutor,
)
from test_apply_reconciler import build_plan


class FakeKubernetesClient:
    def __init__(self):
        self.deleted = []

    def ensure_sandbox(self, desired):
        return RuntimeApplyResult(
            resource_ref="k8s://sandbox/sre-v1",
            changed=True,
            evidence={"uid": "sandbox-uid-001"},
        )

    def delete_sandbox(self, resource_ref):
        self.deleted.append(resource_ref)
        return {"deleted": True}


class FakeTemporalClient:
    def __init__(self):
        self.terminated = []

    def ensure_workflow(self, desired):
        return RuntimeApplyResult(
            resource_ref="temporal://workflow/run-001",
            changed=False,
            evidence={"workflowId": "run-001"},
        )

    def terminate_workflow(self, resource_ref):
        self.terminated.append(resource_ref)
        return {"terminated": True}


def test_kubernetes_executor_maps_runtime_client_to_receipts():
    client = FakeKubernetesClient()
    executor = KubernetesSandboxExecutor(client)
    plan = build_plan()
    binding = plan.bindings["sandbox"]

    receipt = executor.apply(
        plan=plan,
        binding=binding,
        prepared={"kind": "SandboxPlan"},
    )
    rollback = executor.rollback(
        plan=plan,
        binding=binding,
        receipt=receipt,
    )

    assert receipt.resource_ref == "k8s://sandbox/sre-v1"
    assert receipt.evidence["uid"] == "sandbox-uid-001"
    assert rollback.rolled_back is True
    assert client.deleted == ["k8s://sandbox/sre-v1"]


def test_temporal_executor_preserves_idempotent_unchanged_result():
    client = FakeTemporalClient()
    executor = TemporalWorkflowExecutor(client)
    plan = build_plan()
    binding = plan.bindings["workflow"]

    receipt = executor.apply(
        plan=plan,
        binding=binding,
        prepared={"kind": "WorkflowPlan"},
    )

    assert receipt.resource_ref == "temporal://workflow/run-001"
    assert receipt.changed is False
    assert receipt.evidence["workflowId"] == "run-001"
