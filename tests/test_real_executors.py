from agent_control_plane.example_adapters import (
    OpenAIAgentsHarnessAdapter,
    TemporalWorkflowAdapter,
)
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.provider_runtime import ExternalResource
from agent_control_plane.real_executors import (
    OpenAIAgentsHarnessExecutor,
    TemporalWorkflowExecutor,
)


def plan() -> ResolvedReleasePlan:
    return ResolvedReleasePlan(
        release_name="sre-v2",
        bundle_name="sre",
        version="v2",
        bindings={},
    )


class FakeTemporal:
    def __init__(self) -> None:
        self.started = []
        self.terminated = []

    def start_workflow(self, **kwargs):
        self.started.append(kwargs)
        return ExternalResource(
            resource_ref="temporal://agent-run/sre-v2:workflow",
            changed=True,
            external_refs={"temporal.workflow_id": kwargs["workflow_id"]},
            evidence={"state": "started"},
        )

    def terminate_workflow(self, **kwargs):
        self.terminated.append(kwargs)
        return {"state": "terminated", **kwargs}


class FakeHarness:
    def __init__(self) -> None:
        self.created = []
        self.released = []

    def ensure_release(self, **kwargs):
        self.created.append(kwargs)
        return ExternalResource(
            resource_ref="agents://release/sre-v2",
            changed=True,
            external_refs={"agents.release_id": "agents-rel-1"},
            evidence={"state": "ready"},
        )

    def release(self, **kwargs):
        self.released.append(kwargs)
        return {"state": "released", **kwargs}


def test_temporal_executor_preserves_external_identity() -> None:
    binding = {
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
    prepared = TemporalWorkflowAdapter().prepare(plan(), binding)
    transport = FakeTemporal()
    executor = TemporalWorkflowExecutor(transport)

    receipt = executor.apply(plan=plan(), binding=binding, prepared=prepared)

    assert receipt.resource_ref.startswith("temporal://")
    assert receipt.evidence["external_refs"]["temporal.workflow_id"] == "sre-v2:workflow"
    assert transport.started[0]["task_queue"] == "agent-runtime"

    rollback = executor.rollback(plan=plan(), binding=binding, receipt=receipt)
    assert rollback.rolled_back is True
    assert transport.terminated[0]["workflow_id"] == "sre-v2:workflow"


def test_openai_agents_executor_keeps_harness_behind_transport() -> None:
    binding = {
        "metadata": {"name": "harness"},
        "spec": {
            "type": "harness",
            "provider": "openai-agents",
            "capabilities": ["tools", "handoff"],
            "config": {"manifestRef": "sre-investigator"},
        },
    }
    prepared = OpenAIAgentsHarnessAdapter().prepare(plan(), binding)
    transport = FakeHarness()
    executor = OpenAIAgentsHarnessExecutor(transport)

    receipt = executor.apply(plan=plan(), binding=binding, prepared=prepared)

    assert receipt.evidence["external_refs"]["agents.release_id"] == "agents-rel-1"
    assert transport.created[0]["manifest_ref"] == "sre-investigator"
    assert transport.created[0]["capabilities"] == ["tools", "handoff"]

    rollback = executor.rollback(plan=plan(), binding=binding, receipt=receipt)
    assert rollback.rolled_back is True
    assert transport.released[0]["resource_ref"] == "agents://release/sre-v2"
