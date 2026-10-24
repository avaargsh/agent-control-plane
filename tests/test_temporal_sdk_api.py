"""Real Temporal SDK transport acceptance using an isolated SDK dev server.

Kept separate from normal unit tests to avoid external binary downloads and
avoid equating this test server with a production Temporal cluster.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("temporalio")
from temporalio.testing import WorkflowEnvironment

from agent_control_plane.runtime_clients import (
    RuntimeMutationOwnershipUncertain,
    RuntimeMutationUncertain,
)
from agent_control_plane.temporal_runtime_client import TemporalWorkflowClient
from agent_control_plane.temporal_sdk_api import TemporalSDKApi


def _spec(workflow_id: str, incident_id: str = "inc-1") -> dict:
    return {
        "release": "release-a",
        "workflow_id": workflow_id,
        "workflow_type": "ExistingWorkerWorkflow",
        "task_queue": "acp-sdk-adapter-test",
        "input": {"incident_id": incident_id},
    }


def test_sdk_transport_preserves_server_memo_and_rejects_foreign_adoption():
    async def scenario() -> None:
        async with await WorkflowEnvironment.start_local() as env:
            address = env.client.service_client.config.target_host
            api = TemporalSDKApi(target_host=address, namespace=env.client.namespace)
            controller = TemporalWorkflowClient(api)
            spec = _spec("test-sdk/" + uuid.uuid4().hex)

            first = await asyncio.to_thread(controller.ensure_workflow, spec)
            assert first.changed
            assert first.evidence["operationId"].startswith("sha256:")
            assert first.evidence["runId"]

            # The second lookup goes through an independent SDK connection.
            observed = await asyncio.to_thread(api.describe, workflow_id=spec["workflow_id"])
            assert observed["status"] == "RUNNING"
            assert observed["runId"] == first.evidence["runId"]
            assert observed["operationId"] == first.evidence["operationId"]

            repeated = await asyncio.to_thread(controller.ensure_workflow, spec)
            assert not repeated.changed
            with pytest.raises(RuntimeMutationOwnershipUncertain):
                await asyncio.to_thread(
                    controller.ensure_workflow,
                    _spec(spec["workflow_id"], "different-incident"),
                )

            # No second start should silently attach based on workflow ID alone.
            with pytest.raises(RuntimeMutationUncertain):
                await asyncio.to_thread(
                    api.start,
                    workflow_id=spec["workflow_id"],
                    workflow_type=spec["workflow_type"],
                    task_queue=spec["task_queue"],
                    input=spec["input"],
                    operation_id=first.evidence["operationId"],
                )

            terminated = await asyncio.to_thread(controller.terminate_workflow, first.resource_ref)
            assert terminated["workflowId"] == spec["workflow_id"]
            assert terminated["terminationRequested"] is True
            final = await asyncio.to_thread(api.describe, workflow_id=spec["workflow_id"])
            assert final["status"] == "TERMINATED"

    asyncio.run(scenario())


def test_sdk_transport_missing_workflow_is_not_claimed_as_terminated():
    async def scenario() -> None:
        async with await WorkflowEnvironment.start_local() as env:
            api = TemporalSDKApi(
                target_host=env.client.service_client.config.target_host,
                namespace=env.client.namespace,
            )
            missing = "test-sdk/missing-" + uuid.uuid4().hex
            assert await asyncio.to_thread(api.describe, workflow_id=missing) is None
            with pytest.raises(RuntimeMutationUncertain):
                await asyncio.to_thread(
                    api.terminate,
                    workflow_id=missing,
                    reason="test-only",
                )

    asyncio.run(scenario())


def test_sdk_transport_recovers_lost_start_ack_from_server_memo():
    class AfterCommitAckLoss:
        def __init__(self, sdk):
            self.sdk = sdk

        def describe(self, *, workflow_id):
            return self.sdk.describe(workflow_id=workflow_id)

        def start(self, **kwargs):
            self.sdk.start(**kwargs)
            raise RuntimeMutationUncertain("simulated lost start response")

        def terminate(self, **kwargs):
            return self.sdk.terminate(**kwargs)

    async def scenario() -> None:
        async with await WorkflowEnvironment.start_local() as env:
            api = TemporalSDKApi(
                target_host=env.client.service_client.config.target_host,
                namespace=env.client.namespace,
            )
            workflow = _spec("test-sdk/lost-ack-" + uuid.uuid4().hex)
            controller = TemporalWorkflowClient(AfterCommitAckLoss(api))

            recovered = await asyncio.to_thread(controller.ensure_workflow, workflow)
            assert recovered.changed
            assert recovered.evidence["verifiedAfterUncertainMutation"] is True
            assert recovered.evidence["runId"]
            assert recovered.evidence["operationId"] == (
                await asyncio.to_thread(api.describe, workflow_id=workflow["workflow_id"])
            )["operationId"]
            # A second call must attach without running a new Workflow.
            again = await asyncio.to_thread(controller.ensure_workflow, workflow)
            assert again.changed is False
            assert again.evidence["runId"] == recovered.evidence["runId"]

    asyncio.run(scenario())


def test_sdk_transport_rejects_workflow_with_foreign_server_memo():
    async def scenario() -> None:
        async with await WorkflowEnvironment.start_local() as env:
            workflow = _spec("test-sdk/foreign-" + uuid.uuid4().hex)
            await env.client.start_workflow(
                workflow["workflow_type"],
                workflow["input"],
                id=workflow["workflow_id"],
                task_queue=workflow["task_queue"],
                memo={"acp_operation_id": "sha256:" + "e" * 64},
            )
            api = TemporalSDKApi(
                target_host=env.client.service_client.config.target_host,
                namespace=env.client.namespace,
            )
            with pytest.raises(RuntimeMutationOwnershipUncertain) as denied:
                await asyncio.to_thread(
                    TemporalWorkflowClient(api).ensure_workflow, workflow
                )
            assert denied.value.observed_operation_id == "sha256:" + "e" * 64

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "remote",
    ["temporal.example.com:7233", "10.12.0.8:7233", "192.168.1.4:7233"],
)
def test_sdk_reference_requires_loopback_without_tls(remote):
    with pytest.raises(ValueError, match="loopback"):
        TemporalSDKApi(target_host=remote)



def test_sdk_termination_rejects_foreign_operation_and_run_identity():
    async def scenario() -> None:
        async with await WorkflowEnvironment.start_local() as env:
            api = TemporalSDKApi(
                target_host=env.client.service_client.config.target_host,
                namespace=env.client.namespace,
            )
            spec = _spec("test-sdk/fenced-terminate-" + uuid.uuid4().hex)
            first = await asyncio.to_thread(
                TemporalWorkflowClient(api).ensure_workflow, spec
            )
            owner = first.evidence["operationId"]
            run_id = first.evidence["runId"]

            for expected_operation_id, expected_run_id in (
                (None, None),
                ("sha256:" + "f" * 64, run_id),
                (owner, "wrong-run-id"),
            ):
                with pytest.raises(RuntimeMutationOwnershipUncertain):
                    await asyncio.to_thread(
                        api.terminate,
                        workflow_id=spec["workflow_id"],
                        reason="regression-test",
                        expected_operation_id=expected_operation_id,
                        expected_run_id=expected_run_id,
                    )
                observed = await asyncio.to_thread(
                    api.describe, workflow_id=spec["workflow_id"]
                )
                assert observed["status"] == "RUNNING"
                assert observed["runId"] == run_id

            terminated = await asyncio.to_thread(
                TemporalWorkflowClient(api).terminate_workflow,
                first.resource_ref,
                expected_operation_id=owner,
                expected_run_id=run_id,
            )
            assert terminated["terminationRequested"] is True
            assert terminated["runId"] == run_id

    asyncio.run(scenario())
