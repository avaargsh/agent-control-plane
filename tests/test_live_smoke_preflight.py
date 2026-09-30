from agent_control_plane.live_smoke import inspect_live_smoke_environment


def test_preflight_reports_missing_external_environment(monkeypatch):
    for name in (
        "KUBE_CONTEXT",
        "KUBE_NAMESPACE",
        "TEMPORAL_ADDRESS",
        "ACP_KUBE_CONTEXT",
        "ACP_KUBE_NAMESPACE",
        "ACP_TEMPORAL_ADDRESS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "agent_control_plane.live_smoke.shutil.which",
        lambda _: None,
    )

    state = inspect_live_smoke_environment()

    assert state.kubectl is False
    assert state.kube_context is None
    assert state.kube_namespace is None
    assert state.temporal_address is None
    assert state.temporal_reachable is False
    assert state.ready is False


def test_preflight_prefers_stack_wide_environment_names(monkeypatch):
    monkeypatch.setenv("KUBE_CONTEXT", "kind-stack")
    monkeypatch.setenv("KUBE_NAMESPACE", "agent-runtime")
    monkeypatch.setenv("TEMPORAL_ADDRESS", "127.0.0.1:7233")
    monkeypatch.setenv("ACP_KUBE_CONTEXT", "legacy")
    monkeypatch.setenv("ACP_KUBE_NAMESPACE", "legacy")
    monkeypatch.setenv("ACP_TEMPORAL_ADDRESS", "legacy:7233")
    monkeypatch.setattr(
        "agent_control_plane.live_smoke.shutil.which",
        lambda _: "/usr/bin/kubectl",
    )

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "agent_control_plane.live_smoke.socket.create_connection",
        lambda *args, **kwargs: Socket(),
    )

    state = inspect_live_smoke_environment()

    assert state.kube_context == "kind-stack"
    assert state.kube_namespace == "agent-runtime"
    assert state.temporal_address == "127.0.0.1:7233"
    assert state.ready is True
