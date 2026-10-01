from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agent_control_plane.state_transition_protocol import Principal
from agent_control_plane.work_context import (
    SQLiteWorkContextStore,
    WorkStatus,
)


def _run(command: list[str], *, cwd: Path, timeout: int) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(
        command,
        cwd=cwd,
        check=True,
        timeout=timeout,
    )


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(
            f"required CLI is not on PATH: {name}"
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Opt-in live Claude Code -> Codex handoff through the "
            "principal-bound Context MCP server."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(
            ".artifacts/cross-agent-context/live-handoff.db"
        ),
        help=(
            "SQLite path configured in both Claude Code and Codex "
            "Context MCP host entries."
        ),
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Trusted project directory from which both agent CLIs run.",
    )
    parser.add_argument(
        "--work-id",
        default=None,
        help="Optional stable work id; default is generated.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Per-agent subprocess timeout in seconds.",
    )
    return parser.parse_args()


def main() -> int:
    if os.environ.get("AGENT_CONTEXT_LIVE_HANDOFF") != "1":
        print(
            "live handoff is opt-in; set "
            "AGENT_CONTEXT_LIVE_HANDOFF=1",
            file=sys.stderr,
        )
        return 2

    args = _parse_args()
    _require_binary("claude")
    _require_binary("codex")

    cwd = args.cwd.resolve()
    if not cwd.is_dir():
        raise SystemExit(f"cwd does not exist: {cwd}")

    db_path = args.db.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    work_id = args.work_id or f"live-context-{uuid4().hex[:12]}"
    human = Principal(type="human", subject="live-smoke")
    codex = Principal(type="agent", subject="codex")

    store = SQLiteWorkContextStore(db_path)
    created = store.create(
        work_id=work_id,
        namespace=f"repo/{cwd.name}",
        goal=(
            "Prove live cross-agent work continuity: Claude records "
            "implementation evidence and hands the work to Codex; "
            "Codex independently reviews and completes it."
        ),
        actor=human,
        created_at=datetime.now(timezone.utc),
        state={
            "phase": "implementation",
            "live_smoke": True,
            "cwd": str(cwd),
        },
    )

    print(
        f"seeded {created.work_id} at version {created.version} "
        f"in {db_path}",
        flush=True,
    )
    print(
        "Both host configs MUST point AGENT_CONTEXT_DB at this exact path.",
        flush=True,
    )

    claude_prompt = f"""
Use the configured agent-context MCP server as the authoritative work state.
Do not edit the SQLite database directly.

Work id: {work_id}

Perform exactly this coordination sequence:
1. call get_work for the work id;
2. claim_work using the returned current version;
3. record_progress using the returned new version, with:
   state_patch implementation=complete and implementation_agent=claude-code
   evidence_refs containing live:claude:{work_id}
4. handoff_work using the returned new version to principal
   type=agent subject=codex, reason="implementation complete; independent review required",
   and state_patch phase=review.
Finish only after handoff_work succeeds.
""".strip()

    _run(
        [
            "claude",
            "-p",
            "--max-turns",
            "12",
            "--output-format",
            "json",
            claude_prompt,
        ],
        cwd=cwd,
        timeout=args.timeout,
    )

    after_claude = store.get(work_id)
    if after_claude.owner != codex:
        raise SystemExit(
            "Claude run did not hand authoritative ownership to Codex: "
            f"owner={after_claude.owner}"
        )
    if "live:claude:" + work_id not in after_claude.evidence_refs:
        raise SystemExit(
            "Claude run did not attach the required evidence reference"
        )

    codex_prompt = f"""
Use the configured agent-context MCP server as the authoritative work state.
Do not edit the SQLite database directly.

Work id: {work_id}

Perform exactly this coordination sequence:
1. call get_work and verify the current owner is agent:codex;
2. record_progress using the returned current version, with:
   state_patch review=passed and review_agent=codex
   evidence_refs containing live:codex-review:{work_id}
3. complete_work using the returned new version, with:
   evidence_refs containing live:codex-complete:{work_id}
   state_patch phase=done.
Finish only after complete_work succeeds.
""".strip()

    _run(
        [
            "codex",
            "exec",
            "--json",
            codex_prompt,
        ],
        cwd=cwd,
        timeout=args.timeout,
    )

    final = store.get(work_id)
    final.verify()

    expected_evidence = {
        f"live:claude:{work_id}",
        f"live:codex-review:{work_id}",
        f"live:codex-complete:{work_id}",
    }
    missing = expected_evidence.difference(final.evidence_refs)

    if final.status is not WorkStatus.DONE:
        raise SystemExit(
            f"live handoff did not complete: status={final.status.value}"
        )
    if final.owner != codex:
        raise SystemExit(
            f"unexpected final owner: {final.owner}"
        )
    if missing:
        raise SystemExit(
            "live handoff is missing evidence refs: "
            + ", ".join(sorted(missing))
        )

    events = store.changes_since(work_id, 0)
    print(
        "live cross-agent handoff succeeded:",
        {
            "work_id": work_id,
            "version": final.version,
            "status": final.status.value,
            "owner": (
                f"{final.owner.type}:{final.owner.subject}"
                if final.owner
                else None
            ),
            "snapshot_hash": final.snapshot_hash,
            "operations": [event.operation for event in events],
            "evidence_refs": list(final.evidence_refs),
        },
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
