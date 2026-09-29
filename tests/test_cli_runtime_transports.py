import json
from types import SimpleNamespace

from agent_control_plane.cli_runtime_transports import KubectlApi, TemporalCliApi


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
