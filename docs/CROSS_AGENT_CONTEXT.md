# Cross-Agent Work Context

This repository now carries a deliberately small proof for **cross-agent work
continuity**. It is not a vector-memory service and it is not a replacement for
Claude Code, Codex, OpenCode, PowerContext, Mem0, or another agent harness.

The problem being tested is narrower:

> Can one agent hand authoritative work state to another agent without copying
> an entire chat transcript, and can a stale agent be prevented from writing
> after the work has moved on?

## Boundary

The control plane distinguishes four different things:

```text
Event Log   = what happened
Memory      = what may be worth remembering
State       = what is true now
Context     = what a specific agent should see now
```

`work_context.py` owns only the first, third, and a minimal projection of the
fourth:

```text
                 SQLiteWorkContextStore

        authoritative WorkSnapshot (versioned)
                      |
                 CAS / version
                      |
              append-only WorkEvent
                      |
               ContextProjection
                      |
        +-------------+-------------+
        |                           |
   Claude Code                    Codex
```

Long-term semantic memory remains an external context source.

## Protocol objects

### WorkSnapshot

A content-addressed snapshot of the current work item:

- `work_id`
- `namespace`
- immutable `goal`
- monotonic `version`
- `status`
- current `owner`
- structured `state`
- durable `decisions`
- `evidence_refs`
- `snapshot_hash`

The snapshot is the authoritative answer to **what is true now**.

### WorkEvent

Every mutation emits an immutable event that binds:

- `from_version -> to_version`
- actor identity
- operation
- mutation payload
- prior snapshot hash
- resulting snapshot hash

This is the minimal replay/audit chain for work continuity.

### ContextProjection

Agents do not share a prompt blob. They share canonical work state and receive a
projection containing the latest snapshot plus recent events.

Future projection policy can filter or enrich this object with:

- semantic memory
- repository knowledge
- capabilities
- policy scope
- token budgets

The v0 proof intentionally leaves those out.

## Concurrency semantics

Every mutation carries an `expected_version`.

```text
Claude reads v1
Codex  reads v1

Claude claims v1 -> v2

Codex tries claim(v1)
  -> rejected: stale work version
```

This is the minimum requirement for shared agent state. Eventual consistency in
a memory/vector store is not sufficient for work ownership.

## Handoff proof

The reference test executes:

```text
Human creates Work v1
    |
Claude claims -> v2
    |
Claude records progress + evidence -> v3
    |
Claude hands off to Codex -> v4
    |
Codex projects v4 and continues from:
  - current owner
  - structured state
  - decisions
  - evidence refs
  - recent mutation history
```

At the same time a stale Claude write from v2 is rejected after the handoff.

Run:

```bash
pytest tests/test_work_context.py -q
```

## Why this lives here

`agent-control-plane` already owns content-addressed evidence, principals,
durable fencing, execution journals, and replay-verifiable state transitions.
This proof reuses those primitives to establish the narrow boundary between:

```text
Cross-Agent Context Plane
        |
        | work/evidence binding
        v
Agent Control Plane
        |
        | authorized StateTransition
        v
Provider side effect
```

If the protocol proves useful independently, the storage/MCP surface should be
split into its own repository instead of expanding this project into a general
memory or agent framework.

## Next slice

The next implementation should remain small:

1. expose `get_work`, `get_changes_since`, `claim_work`, `record_progress`, and
   `handoff_work` through MCP;
2. add a real Claude Code -> Codex handoff fixture;
3. bind a `ContextProjection.projection_hash` into a proposed
   `StateTransition`, so execution can prove exactly which work snapshot the
   proposing agent saw;
4. keep semantic memory pluggable rather than making it authoritative.
