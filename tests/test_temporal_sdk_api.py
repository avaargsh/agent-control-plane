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
