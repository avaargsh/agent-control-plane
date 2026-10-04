# Agent Control Plane

A thin, evidence-first control plane for **provable execution of AI-initiated state transitions**.

The project is built around three questions:

1. **Was this exact change authorized?**
2. **Did this exact operation own the provider side effect?**
3. **Can an independent verifier prove the resulting state afterwards?**

The core authority object is a **StateTransition / TransitionPlan**, not a prompt,
tool call, model session, workflow step, or provider command.

```text
LLM proposes.
Policy decides.
Control Plane authorizes.
Provider executes.
Evidence proves.
Verifier closes the loop.
```

Agent Control Plane deliberately does **not** become another Agent framework,
MCP gateway, durable workflow engine, sandbox runtime, IAM product, or model
serving layer. Those systems remain external. This repository owns the narrow
execution boundary where an approved state transition becomes a real side
effect and later has to be proven.

## Why this exists

Runtime authorization answers an important question:

```text
May principal P call capability C?
```

That is not enough for high-impact state changes.

A production control plane also has to answer:

```text
Which exact plan was approved?
Was the live resource still the version that was approved?
What happens if the provider committed but the ACK was lost?
Can a retry create a second side effect?
Did this operation actually cause the observed result?
Can that claim be verified without trusting the original Agent process?
```

Two invariants define the project:

> **Desired state reached does not imply operation ownership proven.**

> **A provider acknowledgement is not independent outcome evidence.**

## Normative v0.1 execution chain

```text
ObservationSnapshot
  -> TransitionPlan
  -> PlanAuthorizationBinding
  -> PlanExecutionFence
  -> durable PREPARED ExecutionAttempt
  -> provider side effect
  -> COMMITTED | ABORTED | UNKNOWN
  -> reconciliation
  -> fresh independent ObservationSnapshot
  -> VerificationReport
  -> IndependentExecutionProof
```

The LLM, Agent session, workflow history, and hidden reasoning are not authority
and are not required to verify a completed execution.

The frozen invariants and explicit non-goals are defined in
[docs/V0.1_FREEZE.md](docs/V0.1_FREEZE.md).

## What the control plane owns

The v0.1 core owns:

- exact-plan authorization;
- execution fencing against stale generation / resource version / lease epoch;
- durable PREPARED and terminal execution identity;
- fail-closed handling of UNKNOWN provider outcomes;
- reconciliation after crash, lost ACK, or takeover;
- fresh provider observation after mutation;
- operation-ownership verification;
- canonical, out-of-process verifiable execution proof.

It does **not** own:

- Agent planning or chat/session orchestration;
- Temporal/Restate continuation semantics;
- Kubernetes or another sandbox runtime;
- MCP/tool gateway semantics;
- enterprise IAM or a new policy language;
- model serving;
- PKI/signing infrastructure;
- production multi-tenancy, quota, or HA in v0.1.

## Reference proof: Kubernetes Deployment 20 -> 30

The live kind acceptance path exercises the full state-transition contract
against a real Kubernetes API server:

```text
live Deployment replicas=20
    -> acquire/project fenced execution lease
    -> fresh live generation/resourceVersion observation
    -> freeze EvidenceBundle + StateTransition 20 -> 30
    -> deterministic PolicyDecision + signed approval
    -> exact TransitionPlan
    -> PlanAuthorizationBinding + PlanExecutionFence
    -> durable PREPARED ExecutionAttempt
    -> kubectl merge PATCH using that exact plan
    -> process-boundary reconciliation
    -> fresh Deployment + Pods + Events observation
    -> ownership markers projected into ObservationSnapshot
    -> DesiredStateReached + OperationOwnershipProven
    -> VerificationReport
    -> IndependentExecutionProof
    -> fresh-process verification against trusted statement hash
    -> SUCCEEDED
```

Kubernetes operation ownership is derived from a **fresh observation** and is
bound to durable execution identity through control-plane ownership markers,
including operation, action, transition, plan, and authority-reservation
hashes. A Deployment that merely happens to reach 30 replicas is insufficient.

The same provider-neutral TransitionPlan / execution identity boundary is also
exercised with a GitHub pull-request merge provider. Provider integrations must
conform to the same control-plane contract rather than introduce their own
authority model.

## Quick start

Requirements: Python 3.11+.

For the deterministic developer path:

```bash
make setup
make test
make demo
```

The demo is useful for local development, but it is **not** the normative live
provider acceptance proof.

For the live Kubernetes acceptance path with a local kind cluster:

```bash
export KUBE_CONTEXT=kind-agent-transition
make kind-transition-smoke
```

For a clean-clone release rehearsal:

```bash
make fresh-clone-kind-release-rehearsal
```

Normal unit CI does not imply that a live Kubernetes API was exercised.

## Acceptance artifacts

A successful Kubernetes Golden Slice must produce:

```text
execution-attestation.json
independent-execution-proof.json
independent-execution-proof.json.sha256
execution-journal.db
execution-leases.db
work-context.db
summary.json
```

`IndependentExecutionProof/v1` is the normative completed-execution artifact.
`execution-attestation.json` is retained for v0.1 compatibility only.

Verify the serialized proof in a fresh process:

```bash
python scripts/verify_execution_proof.py \
  .artifacts/kubernetes-transition/independent-execution-proof.json \
  --expected-hash-file \
  .artifacts/kubernetes-transition/independent-execution-proof.json.sha256
```

Verify the complete artifact directory:

```bash
python scripts/verify_v01_acceptance_artifacts.py \
  .artifacts/kubernetes-transition
```

The required file set, ownership inputs, statement-hash algorithm, and
verification rules are frozen in
[docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md](docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md).

## Architecture boundary

```text
             proposal / policy / approval
                        |
                        v
              +-------------------+
              | Agent Control     |
              | Plane             |
              |-------------------|
              | TransitionPlan    |
              | Authorization     |
              | ExecutionFence    |
              | ExecutionJournal  |
              | Reconciliation    |
              | Verification      |
              | ExecutionProof    |
              +---------+---------+
                        |
              provider-neutral contract
                        |
          +-------------+-------------+
          |                           |
          v                           v
   Kubernetes provider          GitHub provider
          |                           |
          v                           v
  independent observation      independent observation
          |                           |
          +-------------+-------------+
                        |
                        v
             IndependentExecutionProof
```

External systems can sit around this boundary without being absorbed into the
control plane:

- **Workflow:** Temporal / Restate
- **Sandbox:** Kubernetes / specialized Agent sandboxes
- **Tool transport:** MCP / provider APIs
- **Policy:** OPA / Cedar / existing authorization systems
- **Identity:** OIDC / workload identity systems
- **Signing:** DSSE / Sigstore / KMS or another trusted digest channel
- **Harness:** Codex, Claude Code, or another Agent runtime

The repository supplies narrow bindings and proofs where useful; it does not
reimplement those systems.

## Core v0.1 invariants

1. **Exact-plan authority.** Authorization and fences bind the canonical
   TransitionPlan, not merely a resource or tool name.
2. **Explicit authority generation.** Stale authority generation and stale lease
   epochs are rejected before provider mutation.
3. **Authority survives ambiguity.** Lease expiry alone does not release an
   unresolved side-effect window.
4. **At-least-once execution.** Provider adapters use deterministic identity and
   idempotency; the project does not claim exactly-once side effects.
5. **UNKNOWN fails closed.** Lost ACK or unverifiable provider state cannot be
   converted into success by retry policy.
6. **Independent observation.** Provider mutation ACKs are not outcome proof.
7. **Ownership-aware verification.** Desired state and operation ownership are
   separate verification conditions.
8. **Independent proof.** A completed execution can be verified in a fresh
   process without the original Agent session.

## Public compatibility surface

The v0.1 compatibility promise is deliberately narrow and machine-verifiable.

Run:

```bash
python scripts/verify_v01_public_contract.py
```

The stable surface includes the installed console scripts, documented command
names, snapshotted top-level Python exports and manifest schemas, plus the
`IndependentExecutionProof/v1` wire identity.

See
[docs/V0.1_PUBLIC_COMPATIBILITY.md](docs/V0.1_PUBLIC_COMPATIBILITY.md) and
[release/v0.1-public-contract.json](release/v0.1-public-contract.json).

Internal coordinator classes, SQLite layouts, provider adapter class APIs,
fault-injection helpers, and exact error/log strings are not promoted to v0.1
public compatibility.

## Secondary and compatibility surfaces

The repository contains earlier and adjacent experiments that remain useful for
compatibility or integration testing but **do not define the v0.1 product
boundary**:

- AgentBundle / AgentRelease and release-planning fixtures;
- Federation / provider bindings and deterministic ReleaseEvidence demos;
- EvalGate and decision-evaluation artifact ingestion;
- AgentAuthorityEnvelope inventory / admission commands;
- cross-agent work-context and optional MCP handoff;
- AI Factory acceptance-evidence ingestion;
- Temporal and sandbox reference bindings.

These surfaces must not widen the frozen execution semantics before v0.1.0.
New Agent lifecycle phases, authority abstractions, orchestration layers, or
product surfaces require an explicit post-v0.1 design decision.

### Optional cross-agent context handoff

The optional work-context path keeps authoritative work state separate from
semantic memory and prompt history:

```text
WorkSnapshot + WorkEvent
        -> ContextProjection
        -> AuthorityHead + ContextOverlay
        -> TransitionProposalBinding
        -> Policy / approval / authorization
        -> exact TransitionPlan
        -> provider side effect + reconcile
        -> fresh observation
        -> IndependentExecutionProof
```

An optional local MCP server is available for integration experiments:

```bash
pip install -e '.[mcp]'
export AGENT_CONTEXT_PRINCIPAL_TYPE=agent
export AGENT_CONTEXT_PRINCIPAL_SUBJECT=claude-code
agent-context-mcp
```

See [docs/CROSS_AGENT_CONTEXT.md](docs/CROSS_AGENT_CONTEXT.md).

## Status

The `release/v0.1.0-hardening` line has frozen:

- the provable-execution architecture;
- the Acceptance Artifact Contract;
- the public API/schema/CLI compatibility snapshot;
- Kubernetes and GitHub provider-neutral proof shapes;
- fresh-process proof verification and tamper rejection;
- clean-clone package and Kubernetes acceptance rehearsals.

The release checklist is tracked in
[RELEASE_READINESS.md](RELEASE_READINESS.md). The release branch intentionally
accepts only correctness/security fixes, falsification tests, release
hardening, and provider adapters that conform to the existing contract.

## Repository boundary in the broader AI infrastructure stack

This repository is the state-transition execution-control layer. Adjacent
repositories have narrower responsibilities:

- `cloud-agent-runtime`: reference Run <-> Workflow <-> Sandbox lifecycle binding;
- `agent-decision-lab`: bounded-decision benchmark/gateway experiments;
- `gpu-compute-platform`: lower-layer accelerator workload control plane;
- `ai-factory-engineering`: cross-layer infrastructure commissioning and acceptance.

They may exchange evidence and provenance artifacts, but they do not share
execution ownership or a single source of truth.

See [docs/AI_INFRA_STACK.md](docs/AI_INFRA_STACK.md).

## Development and release

Useful entry points:

- [DEVELOPMENT.md](DEVELOPMENT.md)
- [docs/V0.1_FREEZE.md](docs/V0.1_FREEZE.md)
- [docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md](docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md)
- [docs/V0.1_PUBLIC_COMPATIBILITY.md](docs/V0.1_PUBLIC_COMPATIBILITY.md)
- [RELEASE_READINESS.md](RELEASE_READINESS.md)
- [RELEASE_NOTES.md](RELEASE_NOTES.md)

## Non-goal

Agent Control Plane is not trying to replace Temporal, Restate, Codex,
Claude Code, MCP gateways, Kubernetes Agent Sandbox, enterprise IAM/policy
systems, or model-serving platforms.

Its job is narrower:

> **Authorize an exact state transition, fence the real side effect, and produce
> independently verifiable evidence that the authorized operation owned the
> observed outcome.**
