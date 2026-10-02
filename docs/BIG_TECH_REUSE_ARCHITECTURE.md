# Big-Tech Reuse Architecture

## Goal

Keep Agent Control Plane thin.

The project MUST NOT become another agent framework, durable runtime, sandbox
runtime, agent fabric, generic policy engine, or agent hosting platform.

The durable product boundary is the correctness of real-world state transitions:

```text
Proposal
  -> Frozen Observation
  -> External Policy Decision
  -> Authorization Binding
  -> Authority Generation / Reservation
  -> Mutation Fence
  -> Provider Side Effect
  -> Receipt or Ambiguous Outcome
  -> Reconciliation
  -> Independent Observation
  -> Outcome Proof
  -> Execution Attestation
```

The agent is only one proposal source. The same protocol must work for a human,
workflow engine, GitOps controller, SRE agent, coding agent, or another control
plane.

## Reuse Principle

Use large-vendor projects as replaceable providers around the protocol core.

```text
                    Proposal / Reasoning
   Microsoft Agent Framework | Google ADK | AWS Strands | Codex
                              |
                              v
                    Proposal Source Adapter
                              |
                              v
+-----------------------------------------------------------------+
|                Agent Control Plane protocol core                 |
|                                                                 |
| EvidenceBundle -> StateTransition -> AuthorizationBinding        |
| -> AuthorityGeneration -> AuthorityReservation -> ExecutionFence|
| -> ExecutionAttempt -> Reconcile -> OutcomeProof -> Attestation  |
+----------------------+----------------------+---------------------+
                       |                      |
                       |                      |
                Policy Provider        Runtime / Compute Ref
                       |                      |
               Dogwood / Cedar          Microsoft Durable
               OPA / external API       Google AX/Substrate
                       |                 Temporal / Restate
                       |
                       v
                 Provider Adapter
        Kubernetes | GitHub | Cloud | DB | SaaS
```

The control plane owns bindings and proof. External systems own their native
execution semantics.

## Microsoft

### Reuse

**Microsoft Agent Framework** is a proposal/harness provider.

Use its function middleware boundary to intercept side-effecting tool calls
before execution. A Microsoft tool call that changes external state should be
translated into a canonical `StateTransition` instead of being allowed to call
the provider directly.

Relevant upstream capabilities:

- Agent and function middleware can intercept calls before and after execution.
- Tool approval middleware already implements session-backed approval flows.
- The framework checks that approved arguments are not silently changed before
  execution.
- Workflows support delayed/interactive approval.

**Microsoft Agent Framework Durable Extension** is a continuation provider.

Use it for:

- persistent agent sessions;
- workflow checkpointing;
- crash recovery;
- distributed workers;
- human waits lasting hours or days;
- self-hosted/BYOC or Azure Functions deployment.

Do NOT copy its session/checkpoint state into Agent Control Plane. Store only an
external continuation reference such as:

```text
runtime = "microsoft-durable"
workflow_id
run_id / instance_id
session_id
framework_version
```

The durable runtime resumes reasoning. Agent Control Plane resumes or reconciles
provider side effects.

### Microsoft adapter shape

```text
MAF FunctionMiddleware
      |
      | side-effecting tool call
      v
TransitionProposalAdapter
      |
      v
Agent Control Plane
      |
      +--> external policy decision
      |
      +--> authorize/fence
      |
      +--> provider mutation
      |
      v
Tool result / workflow continuation
```

Microsoft ToolApprovalMiddleware can remain the interaction UX, but an approval
that authorizes a real side effect must be sealed into the control-plane
authorization chain rather than treated as sufficient authority by itself.

Upstream:

- https://github.com/microsoft/agent-framework
- https://github.com/microsoft/agent-framework-durable-extension
- https://learn.microsoft.com/en-us/agent-framework
- https://learn.microsoft.com/en-us/agent-framework/integrations/durable-extension

## Google

### Reuse AX instead of building an agent runtime

Google AX currently exposes four declarative primitives:

- `Task`
- `Workspace`
- `Gateway`
- `Model`

AX owns isolated task execution, workspace materialization, egress fencing,
suspend/resume, task lifecycle, and scale-out orchestration.

Agent Control Plane MUST NOT reproduce these APIs.

Store AX identities as external runtime evidence:

```text
runtime = "google-ax"
atespace
task_name
task_generation/version if available
workspace_ref
gateway_ref
actor_ref
```

AX task lifecycle is not execution authority. A running AX task may execute only
a mutation for which Agent Control Plane still has a valid authorization and
fence.

### Agent Substrate

Use Agent Substrate for sandbox lifecycle, suspend/resume, isolation and worker
allocation. The control plane should never own RAM/filesystem checkpointing.

### Google adapter shape

```text
ADK / other harness
       |
       v
Agent Control Plane
       |
       +--> authorize transition
       |
       +--> create/reference AX Task when isolated compute is required
       |
       +--> provider adapter performs fenced mutation
       |
       v
AX Task can be suspended/resumed independently
```

A useful distinction is:

```text
AX correctness:
"Can this agent task run, survive, suspend and resume?"

Agent Control Plane correctness:
"Was this exact real-world state transition authorized and proven?"
```

Upstream:

- https://github.com/google/ax
- https://github.com/google/ax/blob/main/DESIGN.md

## AWS

### Reuse Dogwood / Cedar as policy providers

Dogwood Local Engine owns durable temporal policy history, ordering and policy
evaluation over prior actions/outcomes. Agent Control Plane MUST NOT implement a
second temporal policy language.

Dogwood answers:

```text
Is this action permitted now, given policy and history?
```

Agent Control Plane answers:

```text
What exact state transition was authorized?
Against what observed state?
Did the provider execute it?
Can the outcome be independently proven?
```

The integration boundary should be a sealed decision reference:

```text
PolicyDecisionRef
  engine = "dogwood"
  policy_digest
  decision_id
  decision
  evaluated_at
  relevant_history_digest / cursor
```

The project binds that decision into `AuthorizationBinding` but does not
re-evaluate Dogwood semantics itself.

Cedar, OPA, AgentCore Policy, or another engine should fit the same provider
interface.

### Other AWS reuse

- Strands Agents: proposal/harness provider.
- TOLAP: object-level tool/data authorization; do not duplicate it.
- AgentCore Runtime/Gateway/Identity: optional managed providers, not product
  responsibilities of this repository.

Upstream:

- https://github.com/dogwood-policy/dogwood-local-engine
- https://aws.amazon.com/blogs/opensource/introducing-the-dogwood-local-engine-temporal-governance-for-agent-actions/

## Cross-vendor composition is the product test

The architecture should deliberately support mixed stacks.

### Golden Slice A: Microsoft + AWS policy + Kubernetes

```text
Microsoft Agent Framework
  -> FunctionMiddleware
  -> StateTransition: Deployment replicas 20 -> 30
  -> Frozen Kubernetes observation
  -> Dogwood/Cedar policy decision
  -> AuthorizationBinding
  -> AuthorityReservation
  -> ExecutionFence
  -> Kubernetes PATCH with resourceVersion/generation precondition
  -> lost-ACK capable ExecutionAttempt journal
  -> fresh Deployment/Pod/Event observation
  -> OutcomeContract
  -> ExecutionAttestation
  -> Microsoft Durable workflow resumes
```

This is the first recommended integration because it proves that the project is
not a Microsoft runtime, an AWS policy engine, or a Kubernetes operator.

### Golden Slice B: Google AX + GitHub

```text
ADK/Codex proposal
  -> Agent Control Plane
  -> external policy
  -> AX Task for isolated compute
  -> StateTransition: PR open@HEAD abc123 -> merged@HEAD abc123
  -> GitHub provider mutation
  -> fresh GitHub observation
  -> proof that the approved HEAD was the merged HEAD
  -> attestation
```

This demonstrates that AX supplies compute while Agent Control Plane supplies
side-effect correctness.

### Golden Slice C: AWS Strands + Microsoft Durable

The same transition protocol should accept a Strands proposal while a Microsoft
Durable workflow owns continuation. No protocol object should encode either
framework's native session model.

## Provider-neutral contracts

Keep adapters narrow.

### ProposalSource

Responsible only for turning framework-specific intent into a transition
proposal.

```text
proposal source
  -> proposer identity/ref
  -> requested target
  -> requested before/after state
  -> runtime continuation ref
```

No provider mutation occurs here.

### PolicyProvider

```text
evaluate(policy_input) -> PolicyDecisionRef
```

Implementations:

- Dogwood
- Cedar
- OPA
- AgentCore Policy
- test/deterministic provider

The core binds the returned decision. It does not own the policy language.

### RuntimeReference

A provenance-only reference.

```text
provider
run_id
session_id
task_id
workflow_id
external_version
```

It MUST NOT become the source of execution authority.

### MutationProvider

Every real provider integration should eventually expose four conceptual
operations:

```text
observe()
validate_precondition()
apply()
reconcile()
```

The important abstraction is not a generic tool call. It is a state transition
with a provider-specific precondition and postcondition.

## Boundary against Google AX and AWS Dogwood

The current #129-#133 implementation remains valuable only where it owns
provider side-effect correctness.

Keep:

- explicit authority generation distinct from context revision;
- deterministic authority reservation;
- execution lease epoch fencing;
- provider mutation precondition validation;
- durable PREPARED / COMMITTED / ABORTED / UNKNOWN attempt journal;
- lost-ACK reconciliation using provider state;
- fail-closed UNKNOWN semantics;
- terminal-proof-based reservation repair;
- independent post-mutation observation;
- outcome contract verification;
- execution attestation.

Do not expand into:

- durable agent sessions;
- workflow checkpoint engines;
- generic distributed actor runtimes;
- sandbox scheduling/suspend-resume;
- temporal policy history;
- agent registry/discovery/fabric;
- generic approval UI;
- generic Agent DSL.

## Repository consequences

### Keep as protocol core

Candidate long-term core modules:

```text
state_transition_protocol.py
authority_reservation.py
execution_fencing.py
execution_journal.py
execution_lifecycle.py
execution_attestation.py
terminal_reservation_repair.py
provider-specific observation / reconciliation contracts
```

### Move toward integrations/reference code

These should not grow into product-owned runtimes:

```text
runtime_clients.py
runtime_executors.py
temporal_runtime_client.py
sandbox_binding.py
context_mcp.py
generic harness bindings
```

Treat them as examples/adapters.

Policy compilation/evaluation should also move toward external policy-provider
integrations. Preserve policy digests and decision provenance, not ownership of
a new general-purpose policy engine.

## Implementation sequence

### R1 - Boundary freeze

- Add this reuse architecture as an explicit product boundary.
- Stop adding new runtime/session/sandbox features.
- Keep #129-#133 semantics unchanged while comparison work continues.

### R2 - Microsoft adapter

Create a minimal Microsoft Agent Framework function middleware example:

```text
side-effecting FunctionTool
 -> middleware intercept
 -> StateTransition request
 -> control-plane execution
 -> FunctionTool result
```

Use Microsoft Durable only for continuation references.

### R3 - External PolicyProvider

Introduce one provider-neutral decision-reference interface.

First real implementation: Dogwood Local Engine.

Do not port Dogwood policy semantics into Python.

### R4 - Google AX reference adapter

Create an AX runtime-reference adapter that can:

- create/reference a Task;
- record Task/Workspace/Gateway identities as provenance;
- observe task state;
- never grant mutation authority.

### R5 - Second mutation provider

Add GitHub PR merge as the second real state-transition provider.

This is more important than adding additional Kubernetes verbs because it tests
whether the protocol is truly provider-neutral.

### R6 - Cross-vendor proof

Run and retain one artifact showing:

```text
Microsoft Agent Framework
 + Dogwood policy
 + Agent Control Plane
 + Kubernetes provider
 + Microsoft Durable continuation
```

and one showing:

```text
Google AX runtime
 + Agent Control Plane
 + GitHub provider
```

## Kill criteria

The protocol core should be killed or reduced to library glue if an upstream
project demonstrates all of the following in one coherent execution contract:

1. authorization bound to an immutable observed-state identity;
2. provider-side mutation fencing against stale/concurrent state;
3. durable operation ownership before the side effect;
4. explicit ambiguous/lost-ACK outcome state;
5. provider-state reconciliation proving applied vs not-applied ownership;
6. fail-closed authority retention while outcome is unknown;
7. independent postcondition observation;
8. replay-verifiable attestation binding authorization, mutation and outcome.

Until then, the project should stay focused on those properties rather than
expanding horizontally.
