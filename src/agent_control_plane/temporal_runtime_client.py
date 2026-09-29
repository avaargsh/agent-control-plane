from __future__ import annotations

from typing import Any, Mapping, Protocol

from .runtime_clients import RuntimeApplyResult


class TemporalApi(Protocol):
    def describe(self, *, workflow_id: str) -> Mapping[str, Any] | None:
        ...

    def start(self, *, workflow_id: str, workflow_type: str, task_queue: str, input: Mapping[str, Any]) -> Mapping[str, Any]:
        ...

    def terminate(self, *, workflow_id: str, reason: str) -> Mapping[str, Any]:
        ...


class TemporalWorkflowClient:
    """SDK-neutral Temporal adapter; Temporal owns continuation, not desired state."""

    def __init__(self, api: TemporalApi, *, task_queue: str = "agent-runtime") -> None:
        self.api = api
        self.task_queue = task_queue

    def ensure_workflow(self, desired: Mapping[str, Any]) -> RuntimeApplyResult:
        release = str(desired["release"])
        workflow_id = str(desired.get("workflow_id") or f"agent-release/{release}")
        existing = self.api.describe(workflow_id=workflow_id)
        if existing is not None:
            return RuntimeApplyResult(
                resource_ref=f"temporal://workflow/{workflow_id}",
                changed=False,
                evidence={
                    "workflowId": workflow_id,
                    "runId": existing.get("runId"),
                    "status": existing.get("status"),
                },
            )

        started = self.api.start(
            workflow_id=workflow_id,
            workflow_type=str(desired.get("workflow_type", "AgentRunWorkflow")),
            task_queue=str(desired.get("task_queue", self.task_queue)),
            input=dict(desired.get("input", {})),
        )
        return RuntimeApplyResult(
            resource_ref=f"temporal://workflow/{workflow_id}",
            changed=True,
            evidence={
                "workflowId": workflow_id,
                "runId": started.get("runId"),
                "status": started.get("status", "RUNNING"),
            },
        )

    def terminate_workflow(self, resource_ref: str) -> Mapping[str, Any]:
        workflow_id = resource_ref.removeprefix("temporal://workflow/")
        return self.api.terminate(
            workflow_id=workflow_id,
            reason="agent control plane compensation",
        )
