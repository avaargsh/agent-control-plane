import pytest

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

    def terminate_workflow(
        self,
        resource_ref,
        *,
        expected_run_id,
    ):
        self.terminated.append(
            (resource_ref, expected_run_id)
        )
        return {
            "terminated": True,
            "runId": expected_run_id,
        }


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



class ExistingKubernetesClient(FakeKubernetesClient):
    def ensure_sandbox(self, desired):
        return RuntimeApplyResult(
            resource_ref="k8s://sandbox/existing",
            changed=False,
            evidence={"uid": "existing-sandbox"},
        )


def test_kubernetes_rollback_does_not_delete_preexisting_sandbox():
    client = ExistingKubernetesClient()
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

    assert receipt.changed is False
    assert rollback.rolled_back is False
    assert rollback.evidence == {
        "skipped": True,
        "reason": "resource-preexisted",
    }
    assert client.deleted == []


def test_temporal_rollback_does_not_terminate_preexisting_workflow():
    client = FakeTemporalClient()
    executor = TemporalWorkflowExecutor(client)
    plan = build_plan()
    binding = plan.bindings["workflow"]

    receipt = executor.apply(
        plan=plan,
        binding=binding,
        prepared={"kind": "WorkflowPlan"},
    )
    rollback = executor.rollback(
        plan=plan,
        binding=binding,
        receipt=receipt,
    )

    assert receipt.changed is False
    assert rollback.rolled_back is False
    assert rollback.evidence == {
        "skipped": True,
        "reason": "resource-preexisted",
    }
    assert client.terminated == []



class UpdatedKubernetesClient(FakeKubernetesClient):
    def __init__(self):
        super().__init__()
        self.restored = []

    def ensure_sandbox(self, desired):
        return RuntimeApplyResult(
            resource_ref="k8s://sandbox/existing",
            changed=True,
            evidence={
                "changeType": "updated",
                "previousManaged": {
                    "apiVersion": "agents.openai.com/v1alpha1",
                    "kind": "Sandbox",
                    "metadata": {
                        "name": "existing",
                        "namespace": "agent-runtime",
                    },
                    "spec": {
                        "isolation": "none",
                        "warmPool": False,
                        "placement": {},
                    },
                },
            },
        )

    def restore_sandbox(self, previous):
        self.restored.append(previous)
        return {"restored": True}


def test_kubernetes_rollback_restores_updated_preexisting_sandbox():
    client = UpdatedKubernetesClient()
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

    assert receipt.changed is True
    assert receipt.evidence["changeType"] == "updated"
    assert rollback.rolled_back is True
    assert rollback.evidence["restored"] is True
    assert client.deleted == []
    assert len(client.restored) == 1
    assert client.restored[0]["spec"]["isolation"] == "none"


class RecoveredKubernetesClient(FakeKubernetesClient):
    def ensure_sandbox(self, desired):
        return RuntimeApplyResult(
            resource_ref="k8s://sandbox/recovered",
            changed=True,
            evidence={
                "changeType": "created",
                "verifiedAfterUncertainMutation": True,
            },
        )


def test_recovered_kubernetes_mutation_receipt_remains_compensatable():
    client = RecoveredKubernetesClient()
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

    assert receipt.changed is True
    assert receipt.evidence["verifiedAfterUncertainMutation"] is True
    assert rollback.rolled_back is True
    assert client.deleted == ["k8s://sandbox/recovered"]


class RecoveredTemporalClient(FakeTemporalClient):
    def ensure_workflow(self, desired):
        return RuntimeApplyResult(
            resource_ref="temporal://workflow/recovered",
            changed=True,
            evidence={
                "workflowId": "recovered",
                "runId": "run-recovered",
                "verifiedAfterUncertainMutation": True,
            },
        )


def test_recovered_temporal_mutation_receipt_remains_compensatable():
    client = RecoveredTemporalClient()
    executor = TemporalWorkflowExecutor(client)
    plan = build_plan()
    binding = plan.bindings["workflow"]

    receipt = executor.apply(
        plan=plan,
        binding=binding,
        prepared={"kind": "WorkflowPlan"},
    )
    rollback = executor.rollback(
        plan=plan,
        binding=binding,
        receipt=receipt,
    )

    assert receipt.changed is True
    assert rollback.rolled_back is True
    assert client.terminated == [
        ("temporal://workflow/recovered", "run-recovered")
    ]



class MissingRunIdTemporalClient(FakeTemporalClient):
    def ensure_workflow(self, desired):
        return RuntimeApplyResult(
            resource_ref="temporal://workflow/missing-run",
            changed=True,
            evidence={
                "workflowId": "missing-run",
            },
        )


def test_temporal_rollback_refuses_changed_receipt_without_run_id():
    client = MissingRunIdTemporalClient()
    executor = TemporalWorkflowExecutor(client)
    plan = build_plan()
    binding = plan.bindings["workflow"]
    receipt = executor.apply(
        plan=plan,
        binding=binding,
        prepared={"kind": "WorkflowPlan"},
    )

    with pytest.raises(
        RuntimeError,
        match="missing runId",
    ):
        executor.rollback(
            plan=plan,
            binding=binding,
            receipt=receipt,
        )

    assert client.terminated == []
