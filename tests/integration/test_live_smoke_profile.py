import os

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("ACP_LIVE_SMOKE") != "1",
    reason="live Kubernetes/Temporal smoke test is opt-in",
)


def test_live_runtime_environment_is_explicitly_configured():
    """Guardrail for the live profile.

    Transport-specific clients are deliberately not fabricated here. When
    ACP_LIVE_SMOKE=1 this test fails fast unless the operator supplied the
    external-system addresses required by integration/LIVE_SMOKE.md.
    """
    assert os.environ["ACP_KUBE_CONTEXT"]
    assert os.environ["ACP_KUBE_NAMESPACE"]
    assert os.environ["ACP_TEMPORAL_ADDRESS"]
