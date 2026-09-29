from agent_control_plane.example_adapters import TemporalWorkflowAdapter
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.provider_runtime import ExternalResource
from agent_control_plane.temporal_executor import TemporalWorkflowExecutor


def plan() -> ResolvedReleasePlan:
    return ResolvedReleasePlan(
        release_name="sre-v1",
        bundle_name="sre",
        version="v1",
        placement={},
        bindings={},
    )


def binding() -> dict:
    return {
        "metadata": {"name": "workflow"},
        "spec": {
            "type": "workflow",
            "provider": "temporal",
            "config": {
                "workflowType": "AgentRunWorkflow",
                "taskQueue": "agent-runtime",
            },
        },
    }


class FakeTemporal:
    def __init__(self, *, changed=True):
        self.changed = changed
        self.started = None
        self.terminated = None

    def start_workflow(self, **kwargs):
        self.started = kwargs
        return ExternalResource(
            resource_ref=f"temporal://workflow/{kwargs['workflow_id']}",
            changed=self.changed,
            external_refs={"temporal.workflow_id": kwargs["workflow_id"]},
            evidence={"state": "started" if self.changed else "adopted-existing"},
        )

    def terminate_workflow(self, **kwargs):
        self.terminated = kwargs
        return {"state": "terminated"}


def test_temporal_adapter_compiles_stable_workflow_identity() -> None:
    prepared = TemporalWorkflowAdapter().prepare(plan(), binding())
    assert prepared["workflow_id"] == "sre-v1-workflow"
    assert prepared["task_queue"] == "agent-runtime"


def test_temporal_executor_preserves_external_identity_and_compensates_owned_start() -> None:
    transport = FakeTemporal()
    executor = TemporalWorkflowExecutor(transport)
    b = binding()
    prepared = TemporalWorkflowAdapter().prepare(plan(), b)

    receipt = executor.apply(plan=plan(), binding=b, prepared=prepared)

    assert receipt.changed is True
    assert receipt.evidence["external_refs"]["temporal.workflow_id"] == "sre-v1-workflow"
    assert transport.started["input"]["release"] == "sre-v1"

    rollback = executor.rollback(plan=plan(), binding=b, receipt=receipt)
    assert rollback.rolled_back is True
    assert transport.terminated["workflow_id"] == "sre-v1-workflow"


def test_temporal_executor_never_terminates_adopted_workflow() -> None:
    transport = FakeTemporal(changed=False)
    executor = TemporalWorkflowExecutor(transport)
    b = binding()
    prepared = TemporalWorkflowAdapter().prepare(plan(), b)
    receipt = executor.apply(plan=plan(), binding=b, prepared=prepared)

    rollback = executor.rollback(plan=plan(), binding=b, receipt=receipt)

    assert rollback.rolled_back is False
    assert transport.terminated is None
