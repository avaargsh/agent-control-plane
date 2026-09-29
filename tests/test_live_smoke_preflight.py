from agent_control_plane.live_smoke import inspect_live_smoke_environment


def test_preflight_reports_missing_external_environment(monkeypatch):
    monkeypatch.delenv("ACP_KUBE_CONTEXT", raising=False)
    monkeypatch.delenv("ACP_TEMPORAL_ADDRESS", raising=False)
    monkeypatch.setattr("agent_control_plane.live_smoke.shutil.which", lambda _: None)

    state = inspect_live_smoke_environment()

    assert state.kubectl is False
    assert state.kube_context is None
    assert state.temporal_address is None
    assert state.temporal_reachable is False
    assert state.ready is False
