import os

import pytest

from agent_control_plane.live_smoke import inspect_live_smoke_environment


pytestmark = pytest.mark.skipif(
    (
        os.getenv("AGENT_STACK_LIVE_SMOKE")
        or os.getenv("ACP_LIVE_SMOKE")
    )
    != "1",
    reason="live Kubernetes/Temporal smoke test is opt-in",
)


def test_live_runtime_environment_is_explicitly_configured():
    """Guardrail for the live profile.

    AGENT_STACK_LIVE_SMOKE=1 fails fast unless the operator supplied
    the stack-wide external-system addresses required by the smoke test.
    """
    state = inspect_live_smoke_environment()
    assert state.kube_context
    assert state.kube_namespace
    assert state.temporal_address
