import json
import subprocess
from types import SimpleNamespace

import pytest

from agent_control_plane.cli_runtime_transports import KubectlApi, TemporalCliApi
from agent_control_plane.runtime_clients import RuntimeMutationUncertain


def test_kubectl_apply_uses_stdin_json(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps({"metadata":{"uid":"u1"}}), stderr="", returncode=0)
    monkeypatch.setattr("agent_control_plane.cli_runtime_transports.subprocess.run", run)

    result = KubectlApi(context="kind-acp").apply(namespace="agent-runtime", manifest={"kind":"Sandbox"})

    assert result["metadata"]["uid"] == "u1"
    assert calls[0][0][:3] == ["kubectl","--context","kind-acp"]
    assert json.loads(calls[0][1]["input"])["kind"] == "Sandbox"


def test_temporal_start_preserves_cli_run_id(monkeypatch):
    def run(command, **kwargs):
        return SimpleNamespace(stdout=json.dumps({"runId":"run-live-001"}), stderr="", returncode=0)
    monkeypatch.setattr("agent_control_plane.cli_runtime_transports.subprocess.run", run)

    result = TemporalCliApi(address="127.0.0.1:7233").start(
        workflow_id="agent-release/demo", workflow_type="AgentRunWorkflow",
        task_queue="agent-runtime", input={"incidentId":"inc-1"},
    )

    assert result["runId"] == "run-live-001"
    assert result["status"] == "RUNNING"


def test_kubectl_apply_classifies_transport_failure_as_uncertain(monkeypatch):
    def run(command, **kwargs):
        return SimpleNamespace(
            stdout="",
            stderr="error: unexpected EOF",
            returncode=1,
            check_returncode=lambda: (_ for _ in ()).throw(
                subprocess.CalledProcessError(1, command)
            ),
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    with pytest.raises(RuntimeMutationUncertain):
        KubectlApi(context="kind-acp").apply(
            namespace="agent-runtime",
            manifest={"kind": "Sandbox"},
        )


def test_temporal_start_preserves_semantic_failure(monkeypatch):
    def run(command, **kwargs):
        return SimpleNamespace(
            stdout="",
            stderr="workflow already started",
            returncode=1,
            check_returncode=lambda: (_ for _ in ()).throw(
                subprocess.CalledProcessError(1, command)
            ),
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    with pytest.raises(subprocess.CalledProcessError):
        TemporalCliApi(address="127.0.0.1:7233").start(
            workflow_id="agent-release/demo",
            workflow_type="AgentRunWorkflow",
            task_queue="agent-runtime",
            input={"incidentId": "inc-1"},
        )


def test_kubectl_delete_classifies_uncertain_failure(monkeypatch):
    def run(command, **kwargs):
        return SimpleNamespace(
            stdout="",
            stderr="error: connection reset by peer",
            returncode=1,
            check_returncode=lambda: (_ for _ in ()).throw(
                subprocess.CalledProcessError(1, command)
            ),
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    with pytest.raises(RuntimeMutationUncertain):
        KubectlApi(context="kind-acp").delete(
            namespace="agent-runtime",
            name="demo-sandbox",
        )


def test_temporal_terminate_classifies_uncertain_failure(monkeypatch):
    def run(command, **kwargs):
        return SimpleNamespace(
            stdout="",
            stderr="rpc error: code = Unavailable desc = transport is closing",
            returncode=1,
            check_returncode=lambda: (_ for _ in ()).throw(
                subprocess.CalledProcessError(1, command)
            ),
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    with pytest.raises(RuntimeMutationUncertain):
        TemporalCliApi(address="127.0.0.1:7233").terminate(
            workflow_id="agent-release/demo",
            run_id="run-old-001",
            reason="rollback",
        )



def test_temporal_terminate_scopes_cli_command_to_run_id(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            stdout="",
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    result = TemporalCliApi(
        address="127.0.0.1:7233"
    ).terminate(
        workflow_id="agent-release/demo",
        run_id="run-old-001",
        reason="rollback",
    )

    command = calls[0]
    assert "--workflow-id" in command
    assert "agent-release/demo" in command
    assert "--run-id" in command
    assert "run-old-001" in command
    assert result["runId"] == "run-old-001"


def test_temporal_describe_can_target_exact_run_id(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            stdout=json.dumps({
                "execution": {"runId": "run-old-001"},
                "status": "Terminated",
            }),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    result = TemporalCliApi(
        address="127.0.0.1:7233"
    ).describe(
        workflow_id="agent-release/demo",
        run_id="run-old-001",
    )

    assert "--run-id" in calls[0]
    assert "run-old-001" in calls[0]
    assert result["runId"] == "run-old-001"
