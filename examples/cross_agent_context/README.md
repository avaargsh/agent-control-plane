# Live Claude Code -> Codex Context Handoff

This directory contains concrete host configurations for the local
`agent-context-mcp` server.

The goal is not to share a chat transcript. Both harnesses attach to one
authoritative work store, but each server process has a distinct principal:

```text
Claude Code process
  -> agent-context-mcp
     principal = agent:claude-code
           \
            +--> shared-context.db
           /
Codex process
  -> agent-context-mcp
     principal = agent:codex
```

## 1. Install the MCP extra

```bash
python -m pip install -e '.[mcp]'
```

Confirm the entrypoint is on `PATH`:

```bash
command -v agent-context-mcp
```

## 2. Pick one absolute database path

For example:

```bash
mkdir -p "$HOME/.local/share/agent-context"
export SHARED_CONTEXT_DB="$HOME/.local/share/agent-context/work.db"
```

Replace `/ABSOLUTE/PATH/TO/shared-context.db` in both example configuration
files with the same absolute path.

## 3. Claude Code

Claude Code supports project-scoped stdio MCP servers. Copy
`claude.mcp.json` to the project root as `.mcp.json`, after replacing the
database path.

Equivalent CLI setup:

```bash
claude mcp add --scope project agent-context \
  --env AGENT_CONTEXT_DB="$SHARED_CONTEXT_DB" \
  --env AGENT_CONTEXT_PRINCIPAL_TYPE=agent \
  --env AGENT_CONTEXT_PRINCIPAL_SUBJECT=claude-code \
  -- agent-context-mcp
```

Verify:

```bash
claude mcp get agent-context
claude mcp list
```

## 4. Codex

Merge `codex.config.toml` into the project's `.codex/config.toml` or the
user-level `~/.codex/config.toml`, again replacing the database path.

The important part is that Codex receives:

```text
AGENT_CONTEXT_PRINCIPAL_TYPE=agent
AGENT_CONTEXT_PRINCIPAL_SUBJECT=codex
```

while using the exact same `AGENT_CONTEXT_DB`.

Verify the configured MCP server:

```bash
codex mcp list
```

Codex only loads project-level `.codex/config.toml` from a project it considers
trusted.

## 5. Live handoff smoke

This smoke is intentionally opt-in because it consumes real Claude Code and
Codex runs and depends on their local login state and MCP approvals.

Both host configurations must already point at the same database passed with
`--db`.

```bash
export AGENT_CONTEXT_LIVE_HANDOFF=1

python scripts/live_cross_agent_context_handoff.py \
  --db "$SHARED_CONTEXT_DB" \
  --cwd "$PWD"
```

The script does not decide that the agents succeeded because their final text
sounds convincing. It reads the authoritative store after each run.

Expected state sequence:

```text
Human seed            v1 NEW
Claude claim          v2 ACTIVE owner=claude-code
Claude progress       v3
Claude handoff        v4 ACTIVE owner=codex
Codex review          v5
Codex complete        v6 DONE owner=codex
```

It also requires three evidence references:

```text
live:claude:<work-id>
live:codex-review:<work-id>
live:codex-complete:<work-id>
```

If a model says it finished but did not execute the required MCP state
transitions, the smoke fails.

## Trust boundary

The environment principal is a local reference mechanism. A user who can change
the MCP process configuration can also change that identity.

For a networked/shared service, derive the principal from authenticated
transport or workload identity. Do not accept an `actor` field from the model as
proof of identity.
