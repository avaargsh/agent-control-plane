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
        "append_context",
        "get_context_overlay",
        "get_context_changes_since",
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


def test_in_process_mcp_clients_share_one_authoritative_store(tmp_path):
    from mcp.client import Client

    store = SQLiteWorkContextStore(tmp_path / "context.db")
    human_server = build_mcp_server(
        store=store,
        principal=HUMAN,
        clock=_clock(NOW),
    )
    claude_server = build_mcp_server(
        store=store,
        principal=CLAUDE,
        clock=_clock(NOW + timedelta(seconds=1)),
    )
    codex_server = build_mcp_server(
        store=store,
        principal=CODEX,
        clock=_clock(NOW + timedelta(seconds=2)),
    )

    async def scenario():
        async with Client(human_server) as human_client:
            created_result = await human_client.call_tool(
                "create_work",
                {
                    "work_id": "mcp-handoff-1",
                    "namespace": "repo/demo",
                    "goal": "Claude implements; Codex reviews",
                    "state": {"phase": "implementation"},
                },
            )
            assert created_result.is_error is False
            created = created_result.structured_content
            assert created is not None

        async with Client(claude_server) as claude_client:
            claimed_result = await claude_client.call_tool(
                "claim_work",
                {
                    "work_id": "mcp-handoff-1",
                    "expected_version": created["version"],
                },
            )
            assert claimed_result.is_error is False
            claimed = claimed_result.structured_content
            assert claimed is not None

            progress_result = await claude_client.call_tool(
                "record_progress",
                {
                    "work_id": "mcp-handoff-1",
                    "expected_version": claimed["version"],
                    "state_patch": {
                        "implementation": "complete",
                        "tests": "passing",
                    },
                    "evidence_refs": ["test:pytest:mcp-1"],
                },
            )
            assert progress_result.is_error is False
            progressed = progress_result.structured_content
            assert progressed is not None

            handoff_result = await claude_client.call_tool(
                "handoff_work",
                {
                    "work_id": "mcp-handoff-1",
                    "expected_version": progressed["version"],
                    "to_principal_type": "agent",
                    "to_principal_subject": "codex",
                    "reason": "Independent review required",
                    "state_patch": {"phase": "review"},
                },
            )
            assert handoff_result.is_error is False
            handed_off = handoff_result.structured_content
            assert handed_off is not None

        async with Client(codex_server) as codex_client:
            projection_result = await codex_client.call_tool(
                "get_work",
                {"work_id": "mcp-handoff-1"},
            )
            assert projection_result.is_error is False
            projection = projection_result.structured_content
            assert projection is not None

            assert projection["consumer"] == {
                "type": "agent",
                "subject": "codex",
            }
            assert projection["work"]["version"] == handed_off["version"]
            assert projection["work"]["owner"] == {
                "type": "agent",
                "subject": "codex",
            }
            assert projection["work"]["state"]["tests"] == "passing"
            assert projection["work"]["state"]["phase"] == "review"

            review_result = await codex_client.call_tool(
                "record_progress",
                {
                    "work_id": "mcp-handoff-1",
                    "expected_version": handed_off["version"],
                    "state_patch": {"review": "passed"},
                    "evidence_refs": ["review:codex:mcp-1"],
                },
            )
            assert review_result.is_error is False

        async with Client(claude_server) as stale_claude:
            stale_result = await stale_claude.call_tool(
                "record_progress",
                {
                    "work_id": "mcp-handoff-1",
                    "expected_version": progressed["version"],
                    "state_patch": {"late_write": True},
                },
            )
            assert stale_result.is_error is True

    asyncio.run(scenario())


def test_mcp_context_overlay_changes_revision_not_authority(tmp_path):
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
        work_id="shared-context-overlay",
        namespace="repo/demo",
        goal="Share context without mutating authority",
    )
    claimed = claude.claim_work(
        work_id="shared-context-overlay",
        expected_version=created["version"],
    )

    entry = codex.append_context(
        work_id="shared-context-overlay",
        expected_revision=0,
        entry_type="review-note",
        payload={"message": "tests look good"},
    )
    overlay = claude.get_context_overlay(
        work_id="shared-context-overlay",
    )
    current = claude.get_work(
        work_id="shared-context-overlay",
    )

    assert entry["actor"] == {
        "type": "agent",
        "subject": "codex",
    }
    assert overlay["context_revision"] == 1
    assert overlay["authority_version"] == claimed["version"]
    assert current["work"]["version"] == claimed["version"]
    assert (
        current["work"]["snapshot_hash"]
        == claimed["snapshot_hash"]
    )

    changes = claude.get_context_changes_since(
        work_id="shared-context-overlay",
        revision=0,
    )
    assert changes["returned_revision"] == 1
    assert changes["entries"][0]["entry_type"] == "review-note"


def test_mcp_context_overlay_rejects_stale_context_revision(tmp_path):
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

    human.create_work(
        work_id="shared-context-overlay-stale",
        namespace="repo/demo",
        goal="Fence context writers independently",
    )
    claude.append_context(
        work_id="shared-context-overlay-stale",
        expected_revision=0,
        entry_type="note",
        payload={"message": "first"},
    )

    with pytest.raises(
        ProtocolViolation,
        match="stale context revision",
    ):
        codex.append_context(
            work_id="shared-context-overlay-stale",
            expected_revision=0,
            entry_type="note",
            payload={"message": "stale"},
        )
