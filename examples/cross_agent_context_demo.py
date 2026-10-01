from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_control_plane.state_transition_protocol import Principal
from agent_control_plane.work_context import SQLiteWorkContextStore


def main() -> None:
    path = Path(".artifacts/cross-agent-context/context.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    now = datetime.now(timezone.utc)
    human = Principal(type="human", subject="demo/human")
    claude = Principal(type="agent", subject="claude-code")
    codex = Principal(type="agent", subject="codex")

    store = SQLiteWorkContextStore(path)
    work = store.create(
        work_id="demo-auth-refactor",
        namespace="repo/payment-api",
        goal="Refactor auth middleware and independently review it",
        actor=human,
        created_at=now,
        state={"acceptance": ["tests pass", "no auth regression"]},
    )
    work = store.claim(
        work_id=work.work_id,
        expected_version=work.version,
        agent=claude,
        claimed_at=now + timedelta(seconds=1),
    )
    work = store.record_progress(
        work_id=work.work_id,
        expected_version=work.version,
        actor=claude,
        updated_at=now + timedelta(seconds=2),
        state_patch={
            "implementation": "complete",
            "tests": "passing",
        },
        decisions=(
            "Keep token validation at the request boundary",
        ),
        evidence_refs=(
            "git:commit:abc123",
            "test:pytest:run-42",
        ),
    )
    work = store.handoff(
        work_id=work.work_id,
        expected_version=work.version,
        from_agent=claude,
        to_agent=codex,
        handed_off_at=now + timedelta(seconds=3),
        reason="Independent review required",
        state_patch={"phase": "review"},
    )
    projection = store.project(
        work_id=work.work_id,
        consumer=codex,
    )

    print(
        json.dumps(
            {
                "work_id": projection.work.work_id,
                "version": projection.work.version,
                "owner": {
                    "type": projection.work.owner.type,
                    "subject": projection.work.owner.subject,
                },
                "status": projection.work.status.value,
                "state": projection.work.state,
                "decisions": projection.work.decisions,
                "evidence_refs": projection.work.evidence_refs,
                "recent_operations": [
                    event.operation for event in projection.recent_events
                ],
                "snapshot_hash": projection.work.snapshot_hash,
                "projection_hash": projection.projection_hash,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
