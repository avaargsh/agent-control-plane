import json
import subprocess
from types import SimpleNamespace

import pytest

from agent_control_plane.cli_runtime_transports import (
    GitHubCliPullRequestApi,
    KubectlApi,
    TemporalCliApi,
)
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
            reason="rollback",
        )


def test_kubectl_deployment_patch_uses_merge_patch_and_resource_version(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            stdout=json.dumps({
                "metadata": {
                    "uid": "uid-payment-api",
                    "generation": 8,
                    "resourceVersion": "101",
                },
                "spec": {"replicas": 30},
            }),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    from agent_control_plane.cli_runtime_transports import KubectlDeploymentApi

    api = KubectlDeploymentApi(context="kind-acp")
    patch = {
        "metadata": {
            "resourceVersion": "100",
            "annotations": {"k": "v"},
        },
        "spec": {"replicas": 30},
    }
    result = api.patch_deployment(
        namespace="prod",
        name="payment-api",
        patch=patch,
    )

    command = calls[0][0]
    assert command[:3] == ["kubectl", "--context", "kind-acp"]
    assert command[3:7] == ["-n", "prod", "patch", "deployment"]
    assert "--type" in command
    assert command[command.index("--type") + 1] == "merge"
    encoded = command[command.index("-p") + 1]
    assert json.loads(encoded)["metadata"]["resourceVersion"] == "100"
    assert result["spec"]["replicas"] == 30


def test_kubectl_deployment_lists_pods_and_uid_scoped_events(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "pods" in command:
            payload = {"items": [{"metadata": {"name": "pod-a"}}]}
        else:
            payload = {"items": [{"reason": "ScalingReplicaSet"}]}
        return SimpleNamespace(
            stdout=json.dumps(payload),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    from agent_control_plane.cli_runtime_transports import KubectlDeploymentApi

    api = KubectlDeploymentApi(context="kind-acp")
    pods = api.list_pods(namespace="prod", selector="app=payment-api")
    events = api.list_events(
        namespace="prod",
        involved_object_uid="uid-payment-api",
    )

    assert pods[0]["metadata"]["name"] == "pod-a"
    assert events[0]["reason"] == "ScalingReplicaSet"
    assert "-l" in calls[0]
    assert calls[0][calls[0].index("-l") + 1] == "app=payment-api"
    assert "--field-selector" in calls[1]
    assert (
        calls[1][calls[1].index("--field-selector") + 1]
        == "involvedObject.uid=uid-payment-api"
    )


def test_kubectl_deployment_patch_classifies_lost_ack_as_uncertain(monkeypatch):
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

    from agent_control_plane.cli_runtime_transports import KubectlDeploymentApi

    with pytest.raises(RuntimeMutationUncertain):
        KubectlDeploymentApi(context="kind-acp").patch_deployment(
            namespace="prod",
            name="payment-api",
            patch={
                "metadata": {"resourceVersion": "100"},
                "spec": {"replicas": 30},
            },
        )


def test_github_cli_get_pull_request_uses_gh_api(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            stdout=json.dumps({
                "number": 42,
                "state": "open",
                "merged": False,
                "head": {"sha": "abc123"},
            }),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    result = GitHubCliPullRequestApi().get_pull_request(
        owner="acme",
        repo="payments",
        number=42,
    )

    assert result["head"]["sha"] == "abc123"
    assert calls[0][0] == [
        "gh",
        "api",
        "/repos/acme/payments/pulls/42",
    ]
    assert calls[0][1]["check"] is False


def test_github_cli_get_returns_none_for_404(monkeypatch):
    def run(command, **kwargs):
        return SimpleNamespace(
            stdout="",
            stderr="gh: Not Found (HTTP 404)",
            returncode=1,
            check_returncode=lambda: (_ for _ in ()).throw(
                subprocess.CalledProcessError(1, command)
            ),
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    result = GitHubCliPullRequestApi().get_commit(
        owner="acme",
        repo="payments",
        sha="missing",
    )

    assert result is None


def test_github_cli_merge_binds_head_sha_method_and_commit_message(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            stdout=json.dumps({
                "merged": True,
                "sha": "merge123",
                "message": "Pull Request successfully merged",
            }),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "agent_control_plane.cli_runtime_transports.subprocess.run",
        run,
    )

    result = GitHubCliPullRequestApi().merge_pull_request(
        owner="acme",
        repo="payments",
        number=42,
        head_sha="abc123",
        merge_method="squash",
        commit_message=(
            "agent-control-plane-operation:op-42\n"
            "agent-control-plane-plan:plan-hash"
        ),
    )

    command, kwargs = calls[0]
    assert command == [
        "gh",
        "api",
        "--method",
        "PUT",
        "/repos/acme/payments/pulls/42/merge",
        "--input",
        "-",
    ]
    payload = json.loads(kwargs["input"])
    assert payload == {
        "sha": "abc123",
        "merge_method": "squash",
        "commit_message": (
            "agent-control-plane-operation:op-42\n"
            "agent-control-plane-plan:plan-hash"
        ),
    }
    assert result["merged"] is True
    assert result["sha"] == "merge123"


def test_github_cli_merge_classifies_lost_ack_as_uncertain(monkeypatch):
    def run(command, **kwargs):
        return SimpleNamespace(
            stdout="",
            stderr="Post request failed: unexpected EOF",
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
        GitHubCliPullRequestApi().merge_pull_request(
            owner="acme",
            repo="payments",
            number=42,
            head_sha="abc123",
            merge_method="squash",
            commit_message="owned",
        )
