import io
import json

from agent_control_plane.runtime_clients import DecisionGatewayClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_decision_gateway_client(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "action": "EXECUTE",
                "decision": {"candidate": "read_metrics"},
            }
        )

    monkeypatch.setattr(
        "agent_control_plane.runtime_clients.request.urlopen",
        fake_urlopen,
    )

    client = DecisionGatewayClient(
        "http://decision.local",
        timeout_seconds=2.0,
    )
    result = client.decide(
        decision_type="mcp_tool_router",
        candidates=["read_metrics", "read_logs"],
        context={"intent": "cpu"},
    )

    assert captured["url"] == "http://decision.local/decision"
    assert captured["timeout"] == 2.0
    assert result["decision"]["candidate"] == "read_metrics"
