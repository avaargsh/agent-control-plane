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


## MCP surface

The reference context plane is now exposed through the official MCP Python SDK
v2. Local use defaults to stdio. The MCP package remains optional so the core
control-plane library does not depend on an agent protocol runtime.

Install:

```bash
pip install -e '.[mcp]'
```

Each MCP server process is bound to exactly one principal by its environment.
Mutation tools do not accept an actor parameter.

```bash
export AGENT_CONTEXT_DB=$PWD/.artifacts/cross-agent-context/context.db

export AGENT_CONTEXT_PRINCIPAL_TYPE=agent
export AGENT_CONTEXT_PRINCIPAL_SUBJECT=claude-code

agent-context-mcp
```

A second harness can launch the same command against the same database with a
different bound identity:

```bash
export AGENT_CONTEXT_DB=$PWD/.artifacts/cross-agent-context/context.db

export AGENT_CONTEXT_PRINCIPAL_TYPE=agent
export AGENT_CONTEXT_PRINCIPAL_SUBJECT=codex

agent-context-mcp
```

The current tool surface is deliberately small:

- `create_work`
- `get_work`
- `get_changes_since`
- `claim_work`
- `record_progress`
- `handoff_work`
- `complete_work`

`get_work` returns a content-addressed `ContextProjection` rather than a raw
chat transcript. It contains the latest authoritative snapshot plus recent
work events for the bound consumer.

The environment principal is only a local reference identity boundary. A
networked deployment must bind principals from authenticated transport/service
identity rather than trusting caller-supplied environment values.

### MCP handoff contract test

The test suite now drives three in-process MCP clients over the official SDK:

```text
Human MCP client
    create_work(v1)
        |
Claude MCP client
    claim(v1) -> v2
    progress(v2) -> v3
    handoff(Codex, v3) -> v4
        |
Codex MCP client
    get_work() -> ContextProjection(v4)
    progress(v4) -> v5
        |
stale Claude MCP client
    progress(v3) -> ERROR
```

This proves the MCP transport does not weaken the underlying optimistic
concurrency and ownership semantics.

## Next slice

The next implementation should remain small:

1. run the opt-in Claude Code -> Codex live handoff on a configured developer
   workstation and retain its state/evidence artifact;
2. decide whether work freshness needs a narrower authority-generation counter
   than the current conservative whole-snapshot version;
3. integrate the context-bound execution validator into the first real
   provider path rather than relying on an external pre-execution call;
4. keep semantic memory pluggable rather than making it authoritative.



## Context-bound transition proposals

A model proposal can now bind the exact authoritative work context it saw
without changing `StateTransition/v1`.

`TransitionProposalBinding` seals:

- proposer principal
- `transition_hash`
- `work_id`
- monotonic `work_version`
- `work_snapshot_hash`
- `projection_hash`

The proposal hash is inserted under a reserved field in the frozen
`TransitionPolicyInput`. Because a signed `TransitionApproval` already binds
`policy_input_hash`, the existing approval and authorization chain transitively
binds the exact ContextProjection.

```text
WorkSnapshot v17
      |
ContextProjection
      |
projection_hash
      |
TransitionProposalBinding
      |
proposal_hash
      |
TransitionPolicyInput.input_hash
      |
Signed TransitionApproval
      |
AuthorizationBinding
```

The context-bound path fails closed twice:

1. before authorization, the current authoritative work version/hash/owner must
   still match the proposal;
2. immediately before provider execution, the signed approval, frozen policy
   input, proposal binding, current work state, and the existing execution
   fence are re-verified.

This is intentionally conservative: any authoritative work mutation after the
proposal invalidates the proposal. Later versions may distinguish
authority-relevant work generation from non-authoritative notes, but v1 does
not guess.

Run:

```bash
pytest tests/test_context_transition.py -q
```

## Concrete host examples

Ready-to-copy host examples live under
[`examples/cross_agent_context/`](../examples/cross_agent_context/README.md).

They deliberately bind Claude Code to `agent:claude-code` and Codex to
`agent:codex` while pointing both at the same authoritative database.

The repository also carries an opt-in live smoke:

```bash
make context-live-handoff
```

It requires local Claude Code and Codex login/configuration and is not part of
normal CI.
