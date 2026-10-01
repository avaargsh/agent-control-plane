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
- `append_context`
- `get_context_overlay`
- `get_context_changes_since`
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

## Independent context revision overlay

Non-authoritative cross-agent context now has a separate append-only revision
stream instead of being mixed into `WorkSnapshot.state`.

```text
Authoritative Work                    Context Overlay

WorkSnapshot.version = 7              context_revision = 31
owner / status / state                notes / observations / memory hints
decisions / evidence refs             append-only ContextEntry chain
        |                                      |
        +---- authorization freshness          +---- retrieval freshness
```

`SQLiteContextOverlayStore` shares the same SQLite database but uses separate
tables and a separate CAS head. A context append therefore changes
`context_revision` without changing:

- `WorkSnapshot.version`
- `WorkSnapshot.snapshot_hash`
- owner/status
- authoritative state
- an already sealed v1 transition proposal's freshness

MCP exposes:

- `append_context`
- `get_context_overlay`
- `get_context_changes_since`

Every overlay read includes the authoritative work version and snapshot hash
observed in the same SQLite read transaction. This gives a caller a precise
cross-check between the context stream and the authority snapshot it was read
against.

This is deliberately **not** model-selected mutation classification. Agents
cannot mark an arbitrary `record_progress` write as "context-only". Only the
separate overlay API has non-authoritative semantics.

`TransitionProposalBinding/v1` remains supported for compatibility. New
context-aware integrations should use v2 when they need to prove the exact
overlay observed by the proposer.

## Next slice

The next implementation should remain small:

1. run the opt-in Claude Code -> Codex live handoff on a configured developer
   workstation and retain its state/evidence artifact;
2. introduce a v2 proposal binding that records context revision/hash for
   provenance while invalidating only on authoritative WorkSnapshot drift;
3. generalize the attestation producer/consumer interface so additional
   providers can emit the same closure proof without depending on Kubernetes;
4. keep semantic memory pluggable rather than making it authoritative.



## Proposal binding v2: provenance without false invalidation

`TransitionProposalBinding/v2` records both the authoritative work snapshot and
the exact context overlay observed by the proposer:

```text
WorkSnapshot v7 / hash A
        +
ContextOverlay rev30 / head H30 / overlay O30
        |
        v
TransitionProposalBinding/v2
        |
        +--> policy input
        +--> signed approval
        +--> authorization
        +--> ExecutionContextProvenance/v2
```

The freshness rule is intentionally asymmetric:

- authoritative `WorkSnapshot.version/hash/owner/status` drift fails closed;
- later `context_revision` drift does **not** invalidate an already approved
  proposal;
- the original overlay revision/head/hash remains frozen in the proposal,
  policy input, durable PREPARED attempt, terminal receipt and attestation
  chain.

This means the system can prove *what context the model saw* without granting
non-authoritative notes the power to revoke or silently expand execution
authority.

The proposal constructor also rejects a projection and overlay observed against
different authority snapshots. Callers must retry the read instead of sealing a
mixed-time proposal.

The kind live transition intentionally advances the overlay once after
authorization and before the provider side effect. The real Deployment
`20 -> 30` transition must still succeed because authoritative work did not
move.

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


## Kubernetes provider integration

The first real provider path now enforces context provenance inside the
Deployment scale adapter rather than through an external pre-check.

```text
WorkSnapshot
  -> ContextProjection
  -> TransitionProposalBinding
  -> PolicyInput
  -> Signed Approval
  -> Authorization
  -> ExecutionFence
  -> provider reads live generation/resourceVersion
  -> validate_context_bound_execution()
  -> Kubernetes PATCH
  -> fresh Observation
  -> OutcomeContract
```

The provider keeps the existing plain `execute()` entrypoint for
non-context-bound callers and adds `execute_context_bound()` for the stronger
contract. Both first execution and owned replay re-check context freshness.

The kind smoke uses the context-bound path and uploads the SQLite work store
alongside its JSON summary. The summary includes:

- `work_id`
- `work_version`
- `work_snapshot_hash`
- `context_projection_hash`
- `transition_proposal_hash`

This makes the live Kubernetes proof traceable back to the exact cross-agent
work state used by the proposing agent.


## Durable execution context provenance

Context provenance is now persisted before the provider side effect in the
execution write-ahead journal.

A context-bound attempt carries an
`ExecutionContextProvenance/v1` containing:

- `work_id`
- `work_version`
- `work_snapshot_hash`
- `projection_hash`
- `proposal_hash`
- proposer identity
- `provenance_hash`

The provenance object is included in the immutable `attempt_hash`. It
therefore survives controller/process restart as part of the PREPARED record.

When an attempt becomes COMMITTED, ABORTED, or UNKNOWN, the journal
automatically embeds the same provenance under
`_execution_context_provenance` in the terminal result before computing
`result_hash`.

```text
WorkSnapshot
   |
ContextProjection
   |
TransitionProposalBinding
   |
ExecutionContextProvenance
   |
   +--> PREPARED attempt_hash
   |       |
   |    provider side effect
   |       |
   |    process crash
   |       |
   +--> restart / reconcile
           |
       COMMITTED result_hash
           |
   _execution_context_provenance
```

This means crash recovery no longer proves only which transition/action owned
the provider mutation. It also proves which cross-agent work snapshot and
projection produced the authorized attempt.

The SQLite journal migrates older databases by adding nullable provenance
columns. Rows without provenance continue to verify using the original attempt
digest shape.

The kind live smoke now reads the work/projection/proposal hashes back from the
recovered COMMITTED journal record rather than trusting temporary in-process
objects.


## Provider-independent execution attestation

The execution chain now closes with a provider-independent
`ExecutionAttestation/v1`.

It seals the durable execution receipt to independently observed verification:

```text
ExecutionAttempt(COMMITTED)
   + attempt_hash
   + terminal_result_hash
   + context_provenance_hash
          |
          + OutcomeContract hash
          + post-execution EvidenceBundle hash
          + verification status/time
          |
          v
ExecutionAttestation
          |
     attestation_hash
```

The attestation intentionally does not embed Kubernetes-specific fields. It can
be reused by future providers as long as they produce:

- a COMMITTED durable execution attempt;
- an independently collected observation/evidence hash;
- an outcome contract hash;
- a verification result.

For context-bound attempts, the attestation transitively closes the chain back
to the exact work snapshot, projection and proposal through
`context_provenance_hash`.

The live kind transition writes
`.artifacts/kubernetes-transition/execution-attestation.json`, which is
uploaded with the rest of the transition proof.
