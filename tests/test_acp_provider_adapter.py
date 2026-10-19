import pytest

from agent_control_plane.acp_provider_adapter import (
    AcpAdapterViolation,
    AcpSessionBinding,
    AcpV1Normalizer,
    ProviderObservation,
)


BINDING = AcpSessionBinding("tenant-a", "canonical-1", "codex", "acp-opaque-1")


def test_streams_normalize_without_claiming_authority():
    mapper = AcpV1Normalizer()
    out = mapper.update(BINDING, {"sessionId": "acp-opaque-1", "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hello"}
    }})
    assert out.kind == "chat.delta" and out.data == {"text": "hello"}
    assert out.binding.canonical_session_id == "canonical-1"


def test_acp_tool_completion_is_observation_not_execution_proof():
    out = AcpV1Normalizer().update(BINDING, {"sessionId": "acp-opaque-1", "update": {
        "sessionUpdate": "tool_call_update", "toolCallId": "tool-1", "status": "completed"
    }})
    assert out.kind == "tool.observed"
    assert "proof_ref" not in out.data
    assert "status" not in out.data


def test_cross_session_event_denied():
    with pytest.raises(AcpAdapterViolation, match="binding mismatch"):
        AcpV1Normalizer().update(BINDING, {"sessionId": "another", "update": {"sessionUpdate": "plan"}})


def test_unknown_provider_updates_fail_closed():
    with pytest.raises(AcpAdapterViolation, match="unsupported"):
        AcpV1Normalizer().update(BINDING, {"sessionId": "acp-opaque-1", "update": {"sessionUpdate": "unknown"}})


def test_permission_request_is_intent_not_auto_approval():
    intent = AcpV1Normalizer().permission_intent(BINDING, {
        "sessionId": "acp-opaque-1",
        "toolCall": {"toolCallId": "tool-1"},
        "options": [{"optionId": "allow_once"}, {"optionId": "reject"}],
    })
    assert intent.tool_call_id == "tool-1"
    assert intent.option_ids == ("allow_once", "reject")
    assert not hasattr(intent, "approved")


def test_cross_session_permission_rejected():
    with pytest.raises(AcpAdapterViolation):
        AcpV1Normalizer().permission_intent(BINDING, {
            "sessionId": "wrong", "toolCall": {"toolCallId": "tool-1"}, "options": [{"optionId": "allow_once"}]
        })


def test_duplicate_permission_options_rejected():
    with pytest.raises(AcpAdapterViolation):
        AcpV1Normalizer().permission_intent(BINDING, {
            "sessionId": "acp-opaque-1", "toolCall": {"toolCallId": "tool-1"},
            "options": [{"optionId": "allow"}, {"optionId": "allow"}],
        })


def test_provider_observation_cannot_be_written_into_cp_projection():
    from agent_control_plane.ahp_projection import AhpProjection, ProjectionRejected

    observation = ProviderObservation(BINDING, "tool.observed", {"tool_call_id": "tool-1"})
    with pytest.raises(ProjectionRejected, match="only authoritative"):
        AhpProjection().ingest(observation, verify=lambda _: True)
