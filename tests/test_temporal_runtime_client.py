import pytest

from agent_control_plane.runtime_clients import (
    RuntimeMutationOwnershipUncertain,
    RuntimeMutationUncertain,
    TerminalRuntimeConflict,
)
from agent_control_plane.temporal_runtime_client import TemporalWorkflowClient


class FakeTemporalApi:
    def __init__(self):
        self.workflows = {}
        self.starts = 0
        self.terminated = []

    def describe(self, *, workflow_id):
        return self.workflows.get(workflow_id)

    def start(self, *, workflow_id, workflow_type, task_queue, input, operation_id):
        self.starts += 1
        value = {
            "runId": "run-001",
            "status": "RUNNING",
            "operationId": operation_id,
        }
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
    def start(self, *, workflow_id, workflow_type, task_queue, input, operation_id):
        super().start(
            workflow_id=workflow_id,
            workflow_type=workflow_type,
            task_queue=task_queue,
            input=input,
            operation_id=operation_id,
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


class ConcurrentCreatorTemporalApi(FakeTemporalApi):
    def start(self, *, workflow_id, workflow_type, task_queue, input, operation_id):
        self.starts += 1
        self.workflows[workflow_id] = {
            "runId": "run-other-attempt",
            "status": "RUNNING",
            "operationId": "other-attempt",
        }
        raise RuntimeMutationUncertain("rpc error: code = Unavailable")


def test_temporal_client_does_not_claim_concurrent_workflow_after_lost_ack():
    api = ConcurrentCreatorTemporalApi()
    client = TemporalWorkflowClient(api)

    with pytest.raises(RuntimeMutationOwnershipUncertain) as caught:
        client.ensure_workflow(desired())

    assert caught.value.observed_operation_id == "other-attempt"
    assert caught.value.operation_id
    assert api.starts == 1


class UncommittedTemporalApi(FakeTemporalApi):
    def start(self, *, workflow_id, workflow_type, task_queue, input, operation_id):
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


def test_temporal_terminate_fails_closed_if_target_disappears_after_lost_ack():
    api = MissingAfterTerminateApi()
    client = TemporalWorkflowClient(api)
    result = client.ensure_workflow(desired())

    with pytest.raises(RuntimeMutationUncertain):
        client.terminate_workflow(result.resource_ref)



@pytest.mark.parametrize(
    "status",
    [
        "COMPLETED",
        "FAILED",
        "TERMINATED",
        "WORKFLOW_EXECUTION_STATUS_TIMED_OUT",
    ],
)
def test_temporal_client_rejects_terminal_existing_workflow(status):
    api = FakeTemporalApi()
    workflow_id = "agent-release/gpu-xid-remediation-v1"
    api.workflows[workflow_id] = {
        "runId": "run-terminal-001",
        "status": status,
    }
    client = TemporalWorkflowClient(api)

    with pytest.raises(
        TerminalRuntimeConflict,
        match="already terminal",
    ):
        client.ensure_workflow(desired())

    assert api.starts == 0


def test_temporal_client_allows_existing_workflow_with_matching_operation_identity():
    api = FakeTemporalApi()
    client = TemporalWorkflowClient(api)
    first = client.ensure_workflow(desired())
    workflow_id = "agent-release/gpu-xid-remediation-v1"
    api.workflows[workflow_id] = {
        **api.workflows[workflow_id],
        "runId": "run-live-001",
        "status": "RUNNING",
        "operationId": first.evidence["operationId"],
    }
    api.starts = 0

    result = client.ensure_workflow(desired())

    assert result.changed is False
    assert result.evidence["runId"] == "run-live-001"
    assert result.evidence["status"] == "RUNNING"
    assert result.evidence["operationId"] == first.evidence["operationId"]
    assert api.starts == 0



class LostAckTerminalTemporalApi(FakeTemporalApi):
    def start(self, *, workflow_id, workflow_type, task_queue, input, operation_id):
        self.starts += 1
        self.workflows[workflow_id] = {
            "runId": "run-terminal-lost-ack",
            "status": "FAILED",
            "operationId": operation_id,
        }
        raise RuntimeMutationUncertain("rpc error: code = Unavailable")


def test_temporal_client_rejects_terminal_recovery_after_lost_ack():
    api = LostAckTerminalTemporalApi()
    client = TemporalWorkflowClient(api)

    with pytest.raises(
        TerminalRuntimeConflict,
        match="committed but is already terminal",
    ):
        client.ensure_workflow(desired())

    assert api.starts == 1


class ImmediateTerminalTemporalApi(FakeTemporalApi):
    def start(self, *, workflow_id, workflow_type, task_queue, input, operation_id):
        self.starts += 1
        value = {
            "runId": "run-immediate-terminal",
            "status": "COMPLETED",
            "operationId": operation_id,
        }
        self.workflows[workflow_id] = value
        return value


def test_temporal_client_rejects_immediate_terminal_start_result():
    api = ImmediateTerminalTemporalApi()
    client = TemporalWorkflowClient(api)

    with pytest.raises(
        TerminalRuntimeConflict,
        match="start returned terminal execution",
    ):
        client.ensure_workflow(desired())

    assert api.starts == 1



@pytest.mark.parametrize("status", [None, "", "UNKNOWN"])
def test_temporal_client_rejects_unverifiable_existing_status(status):
    api = FakeTemporalApi()
    workflow_id = "agent-release/gpu-xid-remediation-v1"
    api.workflows[workflow_id] = {
        "runId": "run-unknown-001",
        "status": status,
    }
    client = TemporalWorkflowClient(api)

    with pytest.raises(
        TerminalRuntimeConflict,
        match="not safely attachable",
    ):
        client.ensure_workflow(desired())

    assert api.starts == 0


class LostAckUnknownStatusTemporalApi(FakeTemporalApi):
    def start(self, *, workflow_id, workflow_type, task_queue, input, operation_id):
        self.starts += 1
        self.workflows[workflow_id] = {
            "runId": "run-unknown-lost-ack",
            "status": "UNKNOWN",
            "operationId": operation_id,
        }
        raise RuntimeMutationUncertain("rpc error: code = Unavailable")


def test_temporal_client_rejects_unverifiable_recovery_status():
    api = LostAckUnknownStatusTemporalApi()
    client = TemporalWorkflowClient(api)

    with pytest.raises(
        TerminalRuntimeConflict,
        match="not safely recoverable",
    ):
        client.ensure_workflow(desired())

    assert api.starts == 1


def test_temporal_client_rejects_running_workflow_owned_by_another_operation():
    api = FakeTemporalApi()
    workflow_id = "agent-release/gpu-xid-remediation-v1"
    api.workflows[workflow_id] = {
        "runId": "run-foreign-001",
        "status": "RUNNING",
        "operationId": "sha256:" + "f" * 64,
    }
    client = TemporalWorkflowClient(api)

    with pytest.raises(RuntimeMutationOwnershipUncertain) as caught:
        client.ensure_workflow(desired())

    assert caught.value.resource_ref == f"temporal://workflow/{workflow_id}"
    assert caught.value.observed_operation_id == "sha256:" + "f" * 64
    assert caught.value.operation_id != caught.value.observed_operation_id
    assert api.starts == 0


def test_temporal_client_fails_closed_when_running_workflow_has_no_owner_metadata():
    api = FakeTemporalApi()
    workflow_id = "agent-release/gpu-xid-remediation-v1"
    api.workflows[workflow_id] = {
        "runId": "run-unowned-001",
        "status": "RUNNING",
    }
    client = TemporalWorkflowClient(api)

    with pytest.raises(RuntimeMutationOwnershipUncertain) as caught:
        client.ensure_workflow(desired())

    assert caught.value.observed_operation_id is None
    assert api.starts == 0


def test_temporal_client_rejects_same_release_with_different_desired_input():
    api = FakeTemporalApi()
    client = TemporalWorkflowClient(api)
    client.ensure_workflow(desired())

    changed = {
        **desired(),
        "input": {"incidentId": "inc-gpu-xid-002"},
    }
    with pytest.raises(RuntimeMutationOwnershipUncertain):
        client.ensure_workflow(changed)

    assert api.starts == 1


class TemporarilyInvisibleTemporalApi(FakeTemporalApi):
    def __init__(self):
        super().__init__()
        self.operation_ids = []

    def describe(self, *, workflow_id):
        return None

    def start(self, *, workflow_id, workflow_type, task_queue, input, operation_id):
        self.starts += 1
        self.operation_ids.append(operation_id)
        raise RuntimeMutationUncertain("rpc error: code = Unavailable")


def test_temporal_client_reuses_deterministic_operation_identity_across_retries():
    api = TemporarilyInvisibleTemporalApi()
    client = TemporalWorkflowClient(api)

    for _ in range(2):
        with pytest.raises(RuntimeMutationUncertain):
            client.ensure_workflow(desired())

    assert api.starts == 2
    assert api.operation_ids[0] == api.operation_ids[1]


def test_temporal_client_operation_identity_changes_when_desired_payload_changes():
    api = TemporarilyInvisibleTemporalApi()
    client = TemporalWorkflowClient(api)

    for spec in (
        desired(),
        {**desired(), "input": {"incidentId": "inc-gpu-xid-002"}},
    ):
        with pytest.raises(RuntimeMutationUncertain):
            client.ensure_workflow(spec)

    assert len(api.operation_ids) == 2
    assert api.operation_ids[0] != api.operation_ids[1]


class ForeignReceiptTemporalApi(FakeTemporalApi):
    def start(self, *, workflow_id, workflow_type, task_queue, input, operation_id):
        self.starts += 1
        return {
            "runId": "run-foreign-receipt",
            "status": "RUNNING",
            "operationId": "sha256:" + "e" * 64,
        }


def test_temporal_client_rejects_start_receipt_without_matching_owner_identity():
    api = ForeignReceiptTemporalApi()
    client = TemporalWorkflowClient(api)

    with pytest.raises(RuntimeMutationOwnershipUncertain) as caught:
        client.ensure_workflow(desired())

    assert caught.value.observed_operation_id == "sha256:" + "e" * 64
    assert caught.value.operation_id != caught.value.observed_operation_id
    assert api.starts == 1


class OwnershipDeniedTerminalTemporalApi(FakeTemporalApi):
    def terminate(self, *, workflow_id, reason):
        self.workflows[workflow_id]["status"] = "TERMINATED"
        raise RuntimeMutationOwnershipUncertain(
            "cannot authorize termination of foreign Workflow",
            resource_ref=f"temporal://workflow/{workflow_id}",
            operation_id="expected-owner",
            observed_operation_id="foreign-owner",
        )


def test_temporal_termination_ownership_denial_is_never_reconciled_as_success():
    api = OwnershipDeniedTerminalTemporalApi()
    client = TemporalWorkflowClient(api)
    result = client.ensure_workflow(desired())

    with pytest.raises(RuntimeMutationOwnershipUncertain):
        client.terminate_workflow(result.resource_ref)
