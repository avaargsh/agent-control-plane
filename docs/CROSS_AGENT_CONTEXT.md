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

`ContextOverlay/v2` separates **state identity** from the retrieval window.
Its `overlay_hash` binds the observed authority snapshot, context revision and
context head hash, but not `recent_entries`. Reading the same overlay with
`recent_entry_limit=1` or `=8` therefore produces the same overlay identity.

The overlay accepts only explicit non-authoritative entry types such as
`note`, `review`, `review-note`, `summary`, `memory_hint`,
`observation`, and `handoff_note`. Authority-shaped records such as
approval, policy decision, desired state or authorization must use the
authoritative protocol instead.

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
2. validate whether any real WorkSnapshot mutation deserves a separate
   non-authoritative API; do not add one without a concrete workflow;
3. generalize verification producers so additional providers can emit the same
   sealed OutcomeVerificationResult contract;
4. keep semantic memory pluggable rather than making it authoritative.



## Authority generation

The control plane now maintains an explicit `AuthorityHead/v1` beside each
authoritative WorkSnapshot.

```text
WorkSnapshot.version       storage / optimistic concurrency
AuthorityHead.generation   execution-authority freshness
ContextOverlay.revision    non-authoritative context freshness
```

`AuthorityHead` binds:

- `authority_generation`
- an `authority_state_hash` over goal/status/owner/state/decisions/evidence
- the WorkSnapshot and WorkEvent that last advanced authority
- a content-addressed `authority_hash`

Creation and every current authoritative Work mutation update the snapshot,
event and authority head in the **same SQLite transaction**. A mutation that
would only advance version/timestamp without changing the authority-state digest
is rejected as a no-op.

ContextOverlay writes never advance AuthorityGeneration.

For existing databases without an authority head, the store backfills
`generation = current WorkSnapshot.version`. This is intentionally
conservative because all historical WorkSnapshot mutation APIs were treated as
authority-relevant.

### Proposal binding v3

`TransitionProposalBinding/v3` freezes:

```text
Work projection provenance
        +
AuthorityHead generation/state/hash
        +
ContextOverlay revision/head/hash
        |
        v
Policy -> Approval -> Authorization
        |
ExecutionContextProvenance/v3
```

At execution, v3 freshness checks the **current AuthorityHead and current
authority-state digest**, not WorkSnapshot.version. Context revision drift is
still allowed. Work version is retained only as provenance.

Current limitation: the public WorkSnapshot mutation APIs are all
authority-relevant, so generation currently advances with every real Work
mutation. There is deliberately no caller-supplied
`authority_relevant=false` escape hatch. A future non-authoritative Work
mutation must be introduced as a structurally separate API with fields excluded
from the authority-state digest.

## Authority reservation fence

Proposal v3 freshness is protected across the provider mutation window by
`AuthorityReservation/v1`.

```text
ExecutionLease                 AuthorityHead
 provider ownership            generation/hash
       |                            |
       +------------+---------------+
                    |
                    v
          AuthorityReservation
          proposal_hash
          operation_id
          resource_uid
          lease_id / epoch / holder
                    |
            BEGIN IMMEDIATE
                    |
        revalidate proposal v3
                    |
                    v
              Provider PATCH
         + action/transition hash
         + operation id
         + reservation hash
                    |
          successful ownership proof
                    |
                RELEASED
```

The reservation is stored in the same SQLite database as WorkSnapshot and
AuthorityHead. Authoritative Work mutation and reservation acquisition both use
`BEGIN IMMEDIATE`, giving a single linearization point:

- if the authority mutation commits first, reservation acquisition sees a stale
  generation/hash and fails;
- if reservation acquisition commits first, authoritative Work mutation sees
  the ACTIVE reservation and fails;
- ContextOverlay writes remain independent and are not blocked.

The reservation is not a second execution lease. It is bound to the existing
ExecutionLease resource UID, lease ID, epoch, holder and expiry. A higher epoch
for the same resource may supersede an abandoned reservation during controller
takeover only after the durable lease authority confirms the new lease ACTIVE.
Lease expiry alone does not stop the reservation from blocking authority
mutation.

Provider acknowledgement does **not** release the reservation. For proposal v3
the execution journal first persists the reservation binding while the attempt
is PREPARED. The reservation remains ACTIVE across the provider call and any
process-crash window.

A terminal journal transition carries the same reservation binding into the
hashed terminal result. Crash reconciliation accepts APPLIED only when the
provider target contains the exact journal-bound action hash, transition hash,
operation id **and authority reservation hash**. After terminalization the
reservation may be released.

ExecutionLease expiry alone does not unfreeze authority: an in-flight provider
request may still return after its lease deadline. Recovery therefore remains
fail-closed until terminal proof or a durable higher-epoch takeover. Higher
epoch takeover must itself be confirmed ACTIVE by the durable lease authority.

The Kubernetes mutation writes the reservation hash into target annotations.
Independent post-execution observation projects the control-plane ownership
markers into the provider-neutral ObservationSnapshot. v0.1 release acceptance
requires those observed markers to match the durable operation, action,
transition, plan, and authority reservation before
`OperationOwnershipProven=TRUE`.

## Durable execution lifecycle coordinator

The reservation/journal/provider ordering is now exposed through a thin
Kubernetes-specific coordinator rather than requiring every caller to reproduce
the sequence manually.

```text
KubernetesDeploymentExecutionCoordinator.prepare()
    |
    +--> ExecutionJournal PREPARED
    +--> deterministic reservation id from attempt_id
    +--> AuthorityReservation acquire/recover
    +--> durable journal reservation binding
    |
    v
provider side effect
    |
    v
ExecutionJournal COMMITTED
    |
    v
AuthorityReservation RELEASED
```

`execute()` performs the whole normal path. `prepare()` is exposed
separately for crash/recovery tests and controllers that deliberately separate
the durable prepare phase from provider execution.

The coordinator does not make policy, approval, authority, or verification
decisions. It only fixes lifecycle ordering around already-sealed protocol
objects.

The deterministic reservation id also closes the unavoidable cross-database
gap between the Work/Authority SQLite store and the execution journal. If a
process dies after reservation acquisition but before journal binding, retrying
`prepare()` reopens the same PREPARED attempt, recovers the same ACTIVE
reservation, and durably binds it instead of creating another reservation.

Any failure before terminal journal state leaves the PREPARED attempt and
reservation available for replay/reconcile. Successful terminalization releases
the reservation only after the terminal receipt has been durably written.

## Terminal reservation repair

A reservation can remain ACTIVE after the execution journal has already
durably reached COMMITTED or ABORTED if the process crashes between terminal
journal persistence and reservation release.

Repair is deliberately narrow. It does not re-run the provider, rewrite the
terminal receipt, or infer whether an UNKNOWN attempt is safe.

Repair is allowed only when all of the following hold:

- the journal contains a verified COMMITTED or ABORTED attempt;
- the attempt durably binds the same reservation hash and terminal result;
- the reservation is still ACTIVE;
- no higher-epoch durable ACTIVE lease currently claims the resource;
- no other PREPARED journal row references the same reservation hash.

The repair action performs one Authority DB transaction:

```text
AuthorityReservation ACTIVE
        |
        | terminal proof + conflict checks
        v
UPDATE reservation -> RELEASED
INSERT TerminalReservationRepairEvidence/v1
        |
        +-- same SQLite transaction
```

The evidence binds the reservation hash, terminal attempt id/hash/state,
terminal result hash, repair actor and repair timestamp. The terminal
ExecutionAttempt is never modified.

If the evidence insert fails after the RELEASED update, the whole transaction
rolls back and the reservation remains ACTIVE. Retrying after restart is
idempotent because repair evidence is unique per reservation.

UNKNOWN is never accepted as terminal repair proof. Any conflicting lease,
PREPARED attempt, reservation mismatch or evidence mismatch leaves the
reservation unchanged for manual review.

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
  -> project fenced execution lease
  -> fresh provider generation/resourceVersion
  -> exact durable TransitionPlan
  -> validate_context_bound_execution()
  -> Kubernetes PATCH using that plan
  -> reconcile terminal ownership
  -> fresh Observation
  -> ownership-aware VerificationReport
  -> IndependentExecutionProof
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


## Completed-execution proof

`ExecutionAttestation/v2` remains as a compatibility artifact, but it is no
longer the normative v0.1 release closure proof.

The v0.1 Golden Slice closes with:

```text
ExecutionAttempt(COMMITTED)
        |
exact TransitionPlan
+ PlanAuthorizationBinding
+ PlanExecutionFence
        |
fresh ObservationSnapshot
+ provider ownership markers
        |
DesiredStateReached = TRUE
OperationOwnershipProven = TRUE
        |
VerificationReport
        |
IndependentExecutionProof/v1
        |
fresh-process verification
```

This distinction matters because **desired state reached does not prove which
operation produced it**. The Kubernetes reference verifier derives ownership
from the fresh target observation and refuses to seal an
`IndependentExecutionProof` when operation id, action hash, transition hash,
plan hash, or authority reservation hash is missing or mismatched.

The live kind transition still writes
`.artifacts/kubernetes-transition/execution-attestation.json` for compatibility,
but release acceptance is defined by
[`V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md`](V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md)
and the independently verifiable
`independent-execution-proof.json`.
