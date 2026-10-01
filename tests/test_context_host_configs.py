import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "cross_agent_context"


def test_claude_and_codex_configs_share_store_but_not_identity():
    claude = json.loads(
        (EXAMPLES / "claude.mcp.json").read_text(encoding="utf-8")
    )
    codex = tomllib.loads(
        (EXAMPLES / "codex.config.toml").read_text(encoding="utf-8")
    )

    claude_server = claude["mcpServers"]["agent-context"]
    codex_server = codex["mcp_servers"]["agent-context"]

    assert claude_server["command"] == "agent-context-mcp"
    assert codex_server["command"] == "agent-context-mcp"

    claude_env = claude_server["env"]
    codex_env = codex_server["env"]

    assert (
        claude_env["AGENT_CONTEXT_DB"]
        == codex_env["AGENT_CONTEXT_DB"]
    )
    assert claude_env["AGENT_CONTEXT_PRINCIPAL_TYPE"] == "agent"
    assert codex_env["AGENT_CONTEXT_PRINCIPAL_TYPE"] == "agent"
    assert (
        claude_env["AGENT_CONTEXT_PRINCIPAL_SUBJECT"]
        == "claude-code"
    )
    assert (
        codex_env["AGENT_CONTEXT_PRINCIPAL_SUBJECT"]
        == "codex"
    )
    assert (
        claude_env["AGENT_CONTEXT_PRINCIPAL_SUBJECT"]
        != codex_env["AGENT_CONTEXT_PRINCIPAL_SUBJECT"]
    )


def test_codex_config_limits_server_to_context_tool_surface():
    codex = tomllib.loads(
        (EXAMPLES / "codex.config.toml").read_text(encoding="utf-8")
    )
    server = codex["mcp_servers"]["agent-context"]

    assert server["required"] is True
    assert set(server["enabled_tools"]) == {
        "create_work",
        "get_work",
        "get_changes_since",
        "claim_work",
        "record_progress",
        "handoff_work",
        "complete_work",
    }
