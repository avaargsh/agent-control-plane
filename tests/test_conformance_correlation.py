from agent_control_plane.capabilities import (
    ProviderCapabilities,
    check_conformance,
)
from agent_control_plane.correlation import CorrelationContext


def test_conformance_fails_closed_on_missing_capability():
    provider = ProviderCapabilities(
        provider_type="workflow",
        provider_name="temporal",
        capabilities=frozenset({"durable", "retry", "signal"}),
    )
    result = check_conformance(
        required=["durable", "human_wait"],
        provider=provider,
    )
    assert result.compatible is False
    assert result.missing == ("human_wait",)


def test_correlation_keeps_canonical_and_provider_ids_separate():
    ctx = CorrelationContext(
        release_id="sre-v4",
        run_id="run-123",
        session_id="session-7",
        trace_id="0123456789abcdef",
    )
    attrs = ctx.provider_attributes(
        {
            "temporal.workflow_id": "wf-99",
            "temporal.run_id": "tr-1",
        }
    )
    assert attrs["agentplane.run.id"] == "run-123"
    assert attrs["agentplane.external_ref.temporal.workflow_id"] == "wf-99"
    assert attrs["trace.id"] == "0123456789abcdef"
