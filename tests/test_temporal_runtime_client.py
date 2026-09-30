import pytest

from agent_control_plane.runtime_clients import RuntimeMutationUncertain
from agent_control_plane.temporal_runtime_client import TemporalWorkflowClient


class FakeTemporalApi:
    def __init__(self):
        self.workflows = {}
        self.starts = 0
        self.terminated = []

    def describe(self, *, workflow_id):
        return self.workflows.get(workflow_id)

    def start(self, *, workflow_id, workflow_type, task_queue, input):
        self.starts += 1
        value = {"runId": "run-001", "status": "RUNNING"}
        self.workflows[workflow_id] = value
        return value

    def terminate(self, *, workflow_id, reason):
        self.terminated.append((workflow_id, reason))
        return {"terminated": True, "workflowId": workflow_id}


def desired():
    return {
        "release": "gpu-xid-remediation-v1",
        "workflow_type": "GpuXidRemediationWorkflow",
        "input": {"incidentId": "inc-gpu-xid-001"},
    }


def test_temporal_client_starts_once_and_preserves_workflow_identity():
    api = FakeTemporalApi()
    client = TemporalWorkflowClient(api)

    first = client.ensure_workflow(desired())
    second = client.ensure_workflow(desired())

    assert first.changed is True
    assert second.changed is False
    assert api.starts == 1
    assert first.evidence["workflowId"] == "agent-release/gpu-xid-remediation-v1"
    assert second.evidence["runId"] == "run-001"


def test_temporal_client_terminates_by_stable_workflow_identity():
    api = FakeTemporalApi()
    client = TemporalWorkflowClient(api)
    result = client.ensure_workflow(desired())

    terminated = client.terminate_workflow(result.resource_ref)

    assert terminated["terminated"] is True
    assert api.terminated[0][0] == "agent-release/gpu-xid-remediation-v1"


class LostAckTemporalApi(FakeTemporalApi):
    def start(self, *, workflow_id, workflow_type, task_queue, input):
        super().start(
            workflow_id=workflow_id,
            workflow_type=workflow_type,
            task_queue=task_queue,
            input=input,
        )
        raise RuntimeMutationUncertain("rpc error: code = Unavailable")


def test_temporal_client_recovers_start_receipt_after_lost_ack():
    api = LostAckTemporalApi()
    client = TemporalWorkflowClient(api)

    result = client.ensure_workflow(desired())

    assert result.changed is True
    assert result.evidence["workflowId"] == "agent-release/gpu-xid-remediation-v1"
    assert result.evidence["runId"] == "run-001"
    assert result.evidence["verifiedAfterUncertainMutation"] is True


class UncommittedTemporalApi(FakeTemporalApi):
    def start(self, *, workflow_id, workflow_type, task_queue, input):
        raise RuntimeMutationUncertain("connection refused")


def test_temporal_client_propagates_uncertain_error_without_observed_workflow():
    api = UncommittedTemporalApi()
    client = TemporalWorkflowClient(api)

    with pytest.raises(RuntimeMutationUncertain):
        client.ensure_workflow(desired())


class LostAckTerminateApi(FakeTemporalApi):
    def terminate(self, *, workflow_id, reason):
        self.terminated.append((workflow_id, reason))
        self.workflows[workflow_id] = {
            **self.workflows[workflow_id],
            "status": "TERMINATED",
        }
        raise RuntimeMutationUncertain("rpc error: code = Unavailable")


def test_temporal_terminate_recovers_after_lost_ack():
    api = LostAckTerminateApi()
    client = TemporalWorkflowClient(api)
    result = client.ensure_workflow(desired())

    terminated = client.terminate_workflow(result.resource_ref)

    assert terminated["terminated"] is True
    assert terminated["status"] == "TERMINATED"
    assert terminated["verifiedAfterUncertainMutation"] is True


class UncommittedTerminateApi(FakeTemporalApi):
    def terminate(self, *, workflow_id, reason):
        raise RuntimeMutationUncertain("connection refused")


def test_temporal_terminate_propagates_when_workflow_is_still_running():
    api = UncommittedTerminateApi()
    client = TemporalWorkflowClient(api)
    result = client.ensure_workflow(desired())

    with pytest.raises(RuntimeMutationUncertain):
        client.terminate_workflow(result.resource_ref)


class MissingAfterTerminateApi(FakeTemporalApi):
    def terminate(self, *, workflow_id, reason):
        self.workflows.pop(workflow_id, None)
        raise RuntimeMutationUncertain("connection reset")


def test_temporal_terminate_accepts_absent_postcondition_after_uncertain_ack():
    api = MissingAfterTerminateApi()
    client = TemporalWorkflowClient(api)
    result = client.ensure_workflow(desired())

    terminated = client.terminate_workflow(result.resource_ref)

    assert terminated["terminated"] is True
    assert terminated["status"] is None
    assert terminated["verifiedAfterUncertainMutation"] is True
