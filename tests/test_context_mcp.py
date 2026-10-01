import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agent_control_plane.context_mcp import (
    WorkContextToolset,
    _principal_from_env,
    build_mcp_server,
)
from agent_control_plane.state_transition_protocol import (
    Principal,
    ProtocolViolation,
)
from agent_control_plane.work_context import SQLiteWorkContextStore


NOW = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
HUMAN = Principal(type="human", subject="ben")
CLAUDE = Principal(type="agent", subject="claude-code")
CODEX = Principal(type="agent", subject="codex")


def _clock(at):
    return lambda: at


def test_toolset_binds_mutations_to_server_principal(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    human = WorkContextToolset(store, HUMAN, _clock(NOW))
    claude = WorkContextToolset(
        store,
        CLAUDE,
        _clock(NOW + timedelta(seconds=1)),
    )
    codex = WorkContextToolset(
        store,
        CODEX,
        _clock(NOW + timedelta(seconds=2)),
    )

    created = human.create_work(
        work_id="shared-context-1",
        namespace="repo/demo",
        goal="Implement then review auth change",
        state={"phase": "implementation"},
    )
    claimed = claude.claim_work(
        work_id="shared-context-1",
        expected_version=created["version"],
    )

    with pytest.raises(
        ProtocolViolation,
        match="only the current work owner",
    ):
        codex.record_progress(
            work_id="shared-context-1",
            expected_version=claimed["version"],
            state_patch={"phase": "review"},
        )

    handed_off = claude.handoff_work(
        work_id="shared-context-1",
        expected_version=claimed["version"],
        to_principal_type="agent",
        to_principal_subject="codex",
        reason="Implementation complete",
        evidence_refs=["git:commit:123"],
    )

    projected = codex.get_work(work_id="shared-context-1")

    assert handed_off["owner"] == {
        "type": "agent",
        "subject": "codex",
    }
    assert projected["consumer"] == {
        "type": "agent",
        "subject": "codex",
    }
    assert projected["work"]["version"] == handed_off["version"]
    assert "git:commit:123" in projected["work"]["evidence_refs"]


def test_stale_mcp_client_write_fails_after_handoff(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    human = WorkContextToolset(store, HUMAN, _clock(NOW))
    claude = WorkContextToolset(
        store,
        CLAUDE,
        _clock(NOW + timedelta(seconds=1)),
    )
    codex = WorkContextToolset(
        store,
        CODEX,
        _clock(NOW + timedelta(seconds=2)),
    )

    created = human.create_work(
        work_id="shared-context-2",
        namespace="repo/demo",
        goal="Implement and hand off",
    )
    claimed = claude.claim_work(
        work_id="shared-context-2",
        expected_version=created["version"],
    )
    handed_off = claude.handoff_work(
        work_id="shared-context-2",
        expected_version=claimed["version"],
        to_principal_type="agent",
        to_principal_subject="codex",
        reason="Ready for review",
    )

    with pytest.raises(
        ProtocolViolation,
        match="stale work version",
    ):
        claude.record_progress(
            work_id="shared-context-2",
            expected_version=claimed["version"],
            state_patch={"late_write": True},
        )

    reviewed = codex.record_progress(
        work_id="shared-context-2",
        expected_version=handed_off["version"],
        state_patch={"review": "passed"},
        evidence_refs=["review:codex:1"],
    )
    assert reviewed["state"]["review"] == "passed"


def test_get_changes_since_exposes_hash_bound_event_chain(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    human = WorkContextToolset(store, HUMAN, _clock(NOW))
    claude = WorkContextToolset(
        store,
        CLAUDE,
        _clock(NOW + timedelta(seconds=1)),
    )

    created = human.create_work(
        work_id="shared-context-3",
        namespace="repo/demo",
        goal="Track cross-agent history",
    )
    claimed = claude.claim_work(
        work_id="shared-context-3",
        expected_version=created["version"],
    )
    claude.record_progress(
        work_id="shared-context-3",
        expected_version=claimed["version"],
        state_patch={"tests": "passing"},
    )

    changes = claude.get_changes_since(
        work_id="shared-context-3",
        version=1,
    )

    assert changes["current_version"] == 3
    assert [event["operation"] for event in changes["events"]] == [
        "claim",
        "progress",
    ]
    assert (
        changes["events"][1]["prior_snapshot_hash"]
        == changes["events"][0]["snapshot_hash"]
    )


def test_mcp_server_registers_only_explicit_work_context_tools(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    server = build_mcp_server(
        store=store,
        principal=CLAUDE,
        clock=_clock(NOW),
    )

    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}

    assert names == {
        "create_work",
        "get_work",
        "get_changes_since",
        "claim_work",
        "record_progress",
        "handoff_work",
        "complete_work",
    }


def test_principal_identity_must_be_injected_by_server_environment(
    monkeypatch,
):
    monkeypatch.delenv("AGENT_CONTEXT_PRINCIPAL_TYPE", raising=False)
    monkeypatch.delenv("AGENT_CONTEXT_PRINCIPAL_SUBJECT", raising=False)

    with pytest.raises(
        RuntimeError,
        match="AGENT_CONTEXT_PRINCIPAL_TYPE",
    ):
        _principal_from_env()

    monkeypatch.setenv("AGENT_CONTEXT_PRINCIPAL_TYPE", "agent")
    monkeypatch.setenv(
        "AGENT_CONTEXT_PRINCIPAL_SUBJECT",
        "claude-code",
    )

    assert _principal_from_env() == CLAUDE


def test_invalid_status_fails_closed(tmp_path):
    store = SQLiteWorkContextStore(tmp_path / "context.db")
    human = WorkContextToolset(store, HUMAN, _clock(NOW))
    claude = WorkContextToolset(store, CLAUDE, _clock(NOW))

    created = human.create_work(
        work_id="shared-context-4",
        namespace="repo/demo",
        goal="Reject invalid state transition",
    )
    claimed = claude.claim_work(
        work_id="shared-context-4",
        expected_version=created["version"],
    )

    with pytest.raises(
        ProtocolViolation,
        match="unsupported work status",
    ):
        claude.record_progress(
            work_id="shared-context-4",
            expected_version=claimed["version"],
            status="MAGIC",
        )
