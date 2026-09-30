from __future__ import annotations

import json
import subprocess
from typing import Any, Mapping

from .runtime_clients import RuntimeMutationUncertain


_UNCERTAIN_MUTATION_MARKERS = (
    "context deadline exceeded",
    "deadline exceeded",
    "connection reset",
    "connection refused",
    "unexpected eof",
    " eof",
    "i/o timeout",
    "server closed",
    "transport is closing",
    "rpc error: code = unavailable",
    "connection lost",
)


def _mutation_failure_is_uncertain(stderr: str) -> bool:
    message = stderr.lower()
    return any(
        marker in message
        for marker in _UNCERTAIN_MUTATION_MARKERS
    )


def _raise_mutation_failure(completed: subprocess.CompletedProcess[str]) -> None:
    if _mutation_failure_is_uncertain(completed.stderr):
        raise RuntimeMutationUncertain(
            completed.stderr.strip() or "runtime mutation acknowledgement uncertain"
        )
    completed.check_returncode()


def _run_json(command: list[str]) -> Mapping[str, Any]:
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout) if completed.stdout.strip() else {}


class KubectlApi:
    def __init__(self, *, context: str, resource: str = "sandboxes") -> None:
        self.context = context
        self.resource = resource

    def get(self, *, namespace: str, name: str) -> Mapping[str, Any] | None:
        command = ["kubectl", "--context", self.context, "-n", namespace, "get", self.resource, name, "-o", "json"]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            if "NotFound" in completed.stderr or "not found" in completed.stderr.lower():
                return None
            completed.check_returncode()
        return json.loads(completed.stdout)

    def apply(self, *, namespace: str, manifest: Mapping[str, Any]) -> Mapping[str, Any]:
        completed = subprocess.run(
            ["kubectl", "--context", self.context, "-n", namespace, "apply", "-f", "-", "-o", "json"],
            input=json.dumps(manifest), check=False, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            _raise_mutation_failure(completed)
        return json.loads(completed.stdout)

    def delete(self, *, namespace: str, name: str) -> Mapping[str, Any]:
        completed = subprocess.run(
            ["kubectl", "--context", self.context, "-n", namespace, "delete", self.resource, name, "--ignore-not-found=true"],
            check=False, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            _raise_mutation_failure(completed)
        return {"deleted": True, "name": name}


class TemporalCliApi:
    def __init__(self, *, address: str) -> None:
        self.address = address

    def describe(
        self,
        *,
        workflow_id: str,
        run_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        command = [
            "temporal",
            "workflow",
            "describe",
            "--address",
            self.address,
            "--workflow-id",
            workflow_id,
        ]
        if run_id is not None:
            command.extend(["--run-id", run_id])
        command.extend(["--output", "json"])
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            if "not found" in completed.stderr.lower():
                return None
            completed.check_returncode()
        doc = json.loads(completed.stdout)
        execution = doc.get("execution", {})
        return {"runId": execution.get("runId"), "status": doc.get("status")}

    def start(self, *, workflow_id: str, workflow_type: str, task_queue: str, input: Mapping[str, Any]) -> Mapping[str, Any]:
        completed = subprocess.run(
            ["temporal", "workflow", "start", "--address", self.address, "--workflow-id", workflow_id, "--type", workflow_type, "--task-queue", task_queue, "--input", json.dumps(input), "--output", "json"],
            check=False, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            _raise_mutation_failure(completed)
        doc = json.loads(completed.stdout)
        return {"runId": doc.get("runId"), "status": "RUNNING"}

    def terminate(
        self,
        *,
        workflow_id: str,
        run_id: str,
        reason: str,
    ) -> Mapping[str, Any]:
        completed = subprocess.run(
            [
                "temporal",
                "workflow",
                "terminate",
                "--address",
                self.address,
                "--workflow-id",
                workflow_id,
                "--run-id",
                run_id,
                "--reason",
                reason,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            _raise_mutation_failure(completed)
        return {
            "terminated": True,
            "workflowId": workflow_id,
            "runId": run_id,
        }
