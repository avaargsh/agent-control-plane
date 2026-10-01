from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .state_transition_protocol import Principal, ProtocolViolation
from .work_context import (
    ContextProjection,
    SQLiteWorkContextStore,
    WorkEvent,
    WorkSnapshot,
    WorkStatus,
)


Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _principal_dict(principal: Principal | None) -> dict[str, str] | None:
    if principal is None:
        return None
    return {
        "type": principal.type,
        "subject": principal.subject,
    }


def _snapshot_dict(snapshot: WorkSnapshot) -> dict[str, Any]:
    snapshot.verify()
    return {
        "work_id": snapshot.work_id,
        "namespace": snapshot.namespace,
        "goal": snapshot.goal,
        "version": snapshot.version,
        "status": snapshot.status.value,
        "owner": _principal_dict(snapshot.owner),
        "state": dict(snapshot.state),
        "decisions": list(snapshot.decisions),
        "evidence_refs": list(snapshot.evidence_refs),
        "updated_at": snapshot.updated_at.isoformat(),
        "snapshot_hash": snapshot.snapshot_hash,
        "snapshot_version": snapshot.snapshot_version,
    }


def _event_dict(event: WorkEvent) -> dict[str, Any]:
    event.verify()
    return {
        "event_id": event.event_id,
        "work_id": event.work_id,
        "from_version": event.from_version,
        "to_version": event.to_version,
        "actor": _principal_dict(event.actor),
        "operation": event.operation,
        "payload": dict(event.payload),
        "prior_snapshot_hash": event.prior_snapshot_hash,
        "snapshot_hash": event.snapshot_hash,
        "created_at": event.created_at.isoformat(),
        "event_hash": event.event_hash,
        "event_version": event.event_version,
    }


def _projection_dict(projection: ContextProjection) -> dict[str, Any]:
    projection.verify()
    return {
        "consumer": _principal_dict(projection.consumer),
        "work": _snapshot_dict(projection.work),
        "recent_events": [
            _event_dict(event)
            for event in projection.recent_events
        ],
        "projection_hash": projection.projection_hash,
        "projection_version": projection.projection_version,
    }


def _parse_status(value: str | None) -> WorkStatus | None:
    if value is None:
        return None
    try:
        return WorkStatus(value.upper())
    except ValueError as exc:
        allowed = ", ".join(status.value for status in WorkStatus)
        raise ProtocolViolation(
            f"unsupported work status {value!r}; expected one of: {allowed}"
        ) from exc


@dataclass(frozen=True)
class WorkContextToolset:
    """MCP-facing application layer bound to one authenticated principal.

    The principal is injected by the server process, never supplied as a tool
    argument. This prevents an agent from claiming another agent's identity in
    the normal mutation path. The local environment binding is a reference
    mechanism, not a production authentication system.
    """

    store: SQLiteWorkContextStore
    principal: Principal
    clock: Clock = _utc_now

    def create_work(
        self,
        *,
        work_id: str,
        namespace: str,
        goal: str,
        state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = self.store.create(
            work_id=work_id,
            namespace=namespace,
            goal=goal,
            actor=self.principal,
            created_at=self.clock(),
            state=state,
        )
        return _snapshot_dict(snapshot)

    def get_work(
        self,
        *,
        work_id: str,
        recent_event_limit: int = 8,
    ) -> dict[str, Any]:
        projection = self.store.project(
            work_id=work_id,
            consumer=self.principal,
            recent_event_limit=recent_event_limit,
        )
        return _projection_dict(projection)

    def get_changes_since(
        self,
        *,
        work_id: str,
        version: int,
    ) -> dict[str, Any]:
        latest = self.store.get(work_id)
        events = self.store.changes_since(work_id, version)
        return {
            "work_id": work_id,
            "requested_after_version": version,
            "current_version": latest.version,
            "current_snapshot_hash": latest.snapshot_hash,
            "events": [_event_dict(event) for event in events],
        }

    def claim_work(
        self,
        *,
        work_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        snapshot = self.store.claim(
            work_id=work_id,
            expected_version=expected_version,
            agent=self.principal,
            claimed_at=self.clock(),
        )
        return _snapshot_dict(snapshot)

    def record_progress(
        self,
        *,
        work_id: str,
        expected_version: int,
        state_patch: Mapping[str, Any] | None = None,
        decisions: Sequence[str] | None = None,
        evidence_refs: Sequence[str] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.store.record_progress(
            work_id=work_id,
            expected_version=expected_version,
            actor=self.principal,
            updated_at=self.clock(),
            state_patch=state_patch,
            decisions=tuple(decisions or ()),
            evidence_refs=tuple(evidence_refs or ()),
            status=_parse_status(status),
        )
        return _snapshot_dict(snapshot)

    def handoff_work(
        self,
        *,
        work_id: str,
        expected_version: int,
        to_principal_type: str,
        to_principal_subject: str,
        reason: str,
        state_patch: Mapping[str, Any] | None = None,
        evidence_refs: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        target = Principal(
            type=to_principal_type,
            subject=to_principal_subject,
        )
        snapshot = self.store.handoff(
            work_id=work_id,
            expected_version=expected_version,
            from_agent=self.principal,
            to_agent=target,
            handed_off_at=self.clock(),
            reason=reason,
            state_patch=state_patch,
            evidence_refs=tuple(evidence_refs or ()),
        )
        return _snapshot_dict(snapshot)

    def complete_work(
        self,
        *,
        work_id: str,
        expected_version: int,
        evidence_refs: Sequence[str],
        state_patch: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = self.store.complete(
            work_id=work_id,
            expected_version=expected_version,
            agent=self.principal,
            completed_at=self.clock(),
            evidence_refs=tuple(evidence_refs),
            state_patch=state_patch,
        )
        return _snapshot_dict(snapshot)


def build_mcp_server(
    *,
    store: SQLiteWorkContextStore,
    principal: Principal,
    clock: Clock = _utc_now,
):
    """Build the local MCP server without making MCP a core dependency."""

    try:
        from mcp.server import MCPServer
    except ImportError as exc:  # pragma: no cover - packaging failure path
        raise RuntimeError(
            "MCP support is not installed. "
            "Install with: pip install 'agent-control-plane[mcp]'"
        ) from exc

    toolset = WorkContextToolset(
        store=store,
        principal=principal,
        clock=clock,
    )
    server = MCPServer("agent-context")

    @server.tool()
    def create_work(
        work_id: str,
        namespace: str,
        goal: str,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new authoritative work item."""
        return toolset.create_work(
            work_id=work_id,
            namespace=namespace,
            goal=goal,
            state=state,
        )

    @server.tool()
    def get_work(
        work_id: str,
        recent_event_limit: int = 8,
    ) -> dict[str, Any]:
        """Read the latest work state plus recent mutation history."""
        return toolset.get_work(
            work_id=work_id,
            recent_event_limit=recent_event_limit,
        )

    @server.tool()
    def get_changes_since(
        work_id: str,
        version: int,
    ) -> dict[str, Any]:
        """Read append-only work events after a known version."""
        return toolset.get_changes_since(
            work_id=work_id,
            version=version,
        )

    @server.tool()
    def claim_work(
        work_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Claim unowned work using optimistic version fencing."""
        return toolset.claim_work(
            work_id=work_id,
            expected_version=expected_version,
        )

    @server.tool()
    def record_progress(
        work_id: str,
        expected_version: int,
        state_patch: dict[str, Any] | None = None,
        decisions: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Update work owned by this server principal."""
        return toolset.record_progress(
            work_id=work_id,
            expected_version=expected_version,
            state_patch=state_patch,
            decisions=decisions,
            evidence_refs=evidence_refs,
            status=status,
        )

    @server.tool()
    def handoff_work(
        work_id: str,
        expected_version: int,
        to_principal_type: str,
        to_principal_subject: str,
        reason: str,
        state_patch: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Transfer current work ownership to another principal."""
        return toolset.handoff_work(
            work_id=work_id,
            expected_version=expected_version,
            to_principal_type=to_principal_type,
            to_principal_subject=to_principal_subject,
            reason=reason,
            state_patch=state_patch,
            evidence_refs=evidence_refs,
        )

    @server.tool()
    def complete_work(
        work_id: str,
        expected_version: int,
        evidence_refs: list[str],
        state_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Complete owned work; at least one evidence reference is required."""
        return toolset.complete_work(
            work_id=work_id,
            expected_version=expected_version,
            evidence_refs=evidence_refs,
            state_patch=state_patch,
        )

    return server


def _principal_from_env() -> Principal:
    principal_type = os.environ.get("AGENT_CONTEXT_PRINCIPAL_TYPE", "").strip()
    principal_subject = os.environ.get(
        "AGENT_CONTEXT_PRINCIPAL_SUBJECT",
        "",
    ).strip()
    if not principal_type or not principal_subject:
        raise RuntimeError(
            "AGENT_CONTEXT_PRINCIPAL_TYPE and "
            "AGENT_CONTEXT_PRINCIPAL_SUBJECT are required"
        )
    return Principal(
        type=principal_type,
        subject=principal_subject,
    )


def main() -> None:
    db_path = Path(
        os.environ.get(
            "AGENT_CONTEXT_DB",
            ".artifacts/cross-agent-context/context.db",
        )
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    server = build_mcp_server(
        store=SQLiteWorkContextStore(db_path),
        principal=_principal_from_env(),
    )
    server.run()


if __name__ == "__main__":
    main()
