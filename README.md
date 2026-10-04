# Agent Control Plane

An evidence-first control plane for safely authorizing, executing, verifying,
recovering, and replaying AI-initiated state transitions.

The core authority object is a **StateTransition**, not a tool call or provider
command:

```text
LLM proposes.
Policy decides.
Control Plane authorizes.
Provider executes.
Evidence proves.
Verifier closes the loop.
```

It deliberately does **not** become another Agent framework or durable runtime.
Instead, it owns state-transition authorization, desired state, bindings,
policy/approval, evidence, release decisions and replay while execution stays
in specialized runtimes such as Temporal and Kubernetes sandboxes.

## Why this exists

Production Agent systems usually span multiple execution domains:

- a durable workflow engine owns continuation
- a sandbox owns isolated compute
- MCP/tool providers expose capabilities
- a harness owns model/tool interaction semantics
- release policy decides whether an execution is allowed to advance

Without a control plane, those identities and decisions drift apart. This project keeps one provenance chain across them.

## Golden incident

```text
GPU XID Alert
  -> Frozen approved evidence
  -> AgentRelease / Binding DAG
  -> Kubernetes Sandbox
  -> Temporal Workflow
  -> Tool / Harness / Decision bindings
  -> Eval Gate
  -> ReleaseEvidence
  -> Replay Verification
```

The important invariant is not "one framework owns everything". It is that the control plane can prove which release, workflow, sandbox, evidence snapshot and decision belong to the same run.

## Five-minute demo

Requirements: Python 3.11+.

```bash
make setup
make test
make demo
```

The deterministic demo uses in-memory execution and writes:

```text
.artifacts/demo/release-evidence-summary.json
```

It demonstrates approval freeze/resume, ordered binding execution, evaluation, sealed release evidence and replay verification.

## Live smoke boundary

The repository also includes SDK-neutral runtime clients plus CLI transports for Kubernetes and Temporal.

```text
KubernetesSandboxExecutor
  -> KubernetesSandboxClient
  -> KubectlApi
  -> kubectl
  -> Kubernetes API

TemporalWorkflowExecutor
  -> TemporalWorkflowClient
  -> TemporalCliApi
  -> temporal CLI
  -> Temporal service
```

Configure a local environment:

```bash
export KUBE_CONTEXT=kind-agent-control-plane
export KUBE_NAMESPACE=agent-runtime
export TEMPORAL_ADDRESS=127.0.0.1:7233
make preflight
make smoke
```

Normal unit CI does not imply that a live Kubernetes cluster or Temporal server was exercised.

### Live StateTransition proof

The repository also carries a dedicated kind smoke that exercises the
state-transition protocol against a real Kubernetes API server rather than a
fake transport:

```text
live Deployment replicas=20
    -> acquire/project fenced execution lease
    -> fresh live generation/resourceVersion observation
    -> freeze EvidenceBundle + StateTransition 20 -> 30
    -> ContextProjection / TransitionProposalBinding
    -> deterministic PolicyDecision
    -> signed TransitionApproval
    -> AuthorizationBinding + ExecutionFence
    -> durable PREPARED exact TransitionPlan
    -> kubectl merge PATCH using that exact plan
    -> process-boundary reconciliation
    -> fresh Deployment + Pods + Events observation
    -> ownership markers projected into ObservationSnapshot
    -> DesiredStateReached + OperationOwnershipProven
    -> VerificationReport
    -> IndependentExecutionProof
    -> fresh-process proof verification against trusted statement hash
    -> SUCCEEDED
```

The CI workflow is `.github/workflows/kubernetes-transition-smoke.yml`. For a
local kind cluster named `agent-transition`:

```bash
export KUBE_CONTEXT=kind-agent-transition
make kind-transition-smoke
```

The normative v0.1 release evidence is the canonical
`independent-execution-proof.json` plus its trusted statement-hash file.
`execution-attestation.json` is retained only as a compatibility artifact.
The proof binds the exact TransitionPlan, authorization, execution fence,
durable terminal attempt, fresh provider observation, and VerificationReport.
The workflow verifies that serialized proof again in a fresh Python process,
without the original Agent session or execution process.

Kubernetes operation ownership is proved from the fresh observation's
control-plane annotations (operation id, action hash, transition hash, plan
hash, and authority reservation hash), not from the mutation acknowledgement.
**Desired state reached does not imply operation ownership proven.**

The complete required file set, ownership inputs, statement-hash algorithm, and
fresh-process verification command are frozen in
[docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md](docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md).
The live CI gate executes the Golden Slice from a clean clone and records the
source commit plus artifact file digests in `release-evidence.json`.

## Architecture

```text
                         Agent Control Plane

┌─────────────────────────────────────────────────────────────┐
│ AgentRelease / Desired State                                │
│ Provider Bindings                                           │
│ Policy / Approval                                           │
│ Placement / Rollout                                         │
│ Frozen Evidence                                             │
│ Eval Gate                                                   │
│ ReleaseEvidence / Replay                                    │
└───────────────────────┬─────────────────────────────────────┘
                        │ provider-neutral contracts
          ┌─────────────┼───────────────┬────────────────┐
          ▼             ▼               ▼                ▼
      Harness        Workflow         Sandbox           Tools
       Codex         Temporal       Kubernetes           MCP
                        │               │
                  workflowId/runId   sandbox UID
                        └───────┬───────┘
                                ▼
                         ReleaseEvidence
                                ▼
                         Replay Verification
```

## Design principles

- **Thin control plane.** Desired state, policy and release control stay here; provider execution semantics do not.
- **Continuation ownership is explicit.** Temporal/Restate own durable workflow continuation.
- **Sandbox ownership is explicit.** Kubernetes/Agent Sandbox owns isolated execution lifecycle.
- **Evidence is immutable at approval boundaries.** Resume uses the frozen snapshot, not a live reread.
- **Identity is first-class.** Release, workflow, sandbox, evidence and decision identities remain correlated.
- **Replay beats hidden reasoning.** Auditing relies on structured evidence and receipts, not model chain-of-thought.
- **Provider SDKs are optional.** Narrow protocols permit CLI, SDK or service transports.

## Core primitives

- AgentBundle / AgentRelease
- Federation Bindings
- ResolvedReleasePlan
- Provider Registry
- Binding Executors
- FrozenEvidence
- Eval Gates
- ReleaseEvidence
- deterministic replay digest
- AgentAuthorityEnvelope + Fleet/Agent authority inventory
- deployment-time authority drift admission

## Cross-agent context and execution provenance

The optional context path keeps **authoritative work state** separate from
semantic memory and prompt history:

```text
WorkSnapshot + WorkEvent
        ↓
ContextProjection
        ↓
AuthorityHead + ContextOverlay
        ↓
TransitionProposalBinding/v3
        ↓
Policy / signed approval / authorization
        ↓
ExecutionContextProvenance
        ↓
PREPARED attempt → provider side effect → reconcile
        ↓
COMMITTED result
        ↓
fresh observation + OutcomeContract
        ↓
ExecutionAttestation
```

Key properties:

- work updates use version/CAS semantics and an append-only hash-linked event
  history;
- Claude Code, Codex, or another MCP client can share one work store while
  retaining distinct principals;
- the provider execution boundary rejects stale authority generation before
  mutation and on owned replay;
- crash recovery preserves the work/projection/proposal provenance in the
  durable journal;
- the final attestation binds the committed receipt to the exact transition,
  outcome contract, independently collected observation evidence, and a sealed
  successful verification result.

The local MCP server is optional:

```bash
pip install -e '.[mcp]'
export AGENT_CONTEXT_PRINCIPAL_TYPE=agent
export AGENT_CONTEXT_PRINCIPAL_SUBJECT=claude-code
agent-context-mcp
```

See [docs/CROSS_AGENT_CONTEXT.md](docs/CROSS_AGENT_CONTEXT.md) for the protocol,
host configuration, live handoff, and kind proof.

## Repository boundary in the broader AI infrastructure stack

This repository is the **primary Agent Infra control-plane product**. Adjacent repositories have narrower roles:

- `cloud-agent-runtime`: reference Run ↔ Workflow ↔ Sandbox lifecycle binding.
- `agent-decision-lab`: bounded-decision benchmark/gateway experiments.
- `gpu-compute-platform`: lower-layer accelerator workload control plane.
- `ai-factory-engineering`: cross-layer infrastructure commissioning and acceptance.

They share evidence/provenance patterns, but they do not share execution ownership or a single source of truth.

See [docs/AI_INFRA_STACK.md](docs/AI_INFRA_STACK.md) for the ownership matrix, integration rules, and the first cross-project Golden Slice.

## Decision eval evidence gate

The control plane can consume a content-addressed `decision-eval/v1` artifact
produced by `agent-decision-lab`. The artifact digest is verified before any
provider mutation, and only metrics sealed inside the verified artifact are
projected into Eval Gates.

This keeps model evaluation evidence separate from execution authorization:

```text
Decision Lab benchmark
   -> decision-eval/v1 artifact
   -> digest verification
   -> metric projection
   -> Eval Gate
   -> promote / block / rollback

Deterministic Policy / Approval
   -> execution authorization
```

A gate may explicitly require `fallback_measured == 1` so an unmeasured
placeholder System-2 fallback cannot satisfy a release criterion.

## AI Factory acceptance evidence boundary

The control plane can bind an AI Factory
`aifactory.engineering/v1alpha1 AcceptanceArtifact` into
`ReleaseEvidence`.

The consumer verifies the artifact's canonical SHA-256 digest before any provider
mutation, but **does not** treat `accepted=true`, `disposition=ACCEPT`, or
per-gate PASS values as release authorization.

```text
AI Factory
  -> AcceptanceArtifact + digest
  -> Agent Control Plane integrity verification
  -> ReleaseEvidence binding
       integrity_verified = true
       trusted = false
       gate_eligible = false

future trusted attestation verifier
  -> may establish producer authenticity
  -> only then may acceptance become gate-eligible
```

This distinction is deliberate: a content digest proves that bytes did not
change; it does not prove which trusted system produced them.

## Decision eval CLI handoff

A measured Decision Lab artifact can be verified and gated without importing the
Decision Lab package:

```bash
agent-control-plane decision-eval-verify decision-eval.json

agent-control-plane decision-eval-gate \
  examples/decision-system2-eval-gate.yaml \
  --artifact decision-eval.json
```

The consumer validates the content digest and dataset provenance before exposing
metrics to the gate. When `fallback_evaluation.measured=true`, the artifact must
also carry the measured System-2 adapter, threshold, case counts, fallback rate,
accuracy, p50/p95 latency, token usage, and per-case results.

Projected gate metrics use the `system2_` prefix, for example:

- `system2_accuracy`
- `system2_fallback_rate`
- `system2_p95_latency_ms`
- `system2_mean_tokens_processed`

This keeps the fast-path operating-point `fallback_rate` distinct from the
measured System-2 execution metrics.

## Trusted AI Factory acceptance

An AI Factory `AcceptanceArtifact` remains evidence-only until a separate
attestation is verified by an explicitly configured trust verifier.

The reference verifier uses a key-id -> HMAC secret registry for controlled
integration proofs:

```text
AcceptanceArtifact
   -> digest verification
AcceptanceAttestation
   -> artifactDigest binding
   -> keyId lookup in trusted registry
   -> signature verification
        |
        v
factory_acceptance_trusted = 1
factory_accepted = 0 | 1
        |
        v
EvalGate
```

Unsigned artifacts never project these metrics. An attestation with an unknown
key, unsupported algorithm, mismatched artifact digest, or invalid signature
blocks before provider mutation.

The HMAC verifier is deliberately behind the `FactoryAttestationVerifier`
interface. Production deployments should replace it with asymmetric
KMS/Sigstore/Cosign verification rather than sharing HMAC secrets with the
control plane.

See `examples/factory-acceptance-gate.yaml`.

## Live proof artifact admission

The synthetic five-repository fixture proves the contracts compose. A separate
fail-closed command admits artifacts for the **live** proof:

```bash
export AI_FACTORY_ATTESTATION_SECRET='...'

agent-control-plane live-proof-verify \
  --decision-artifact decision-eval.json \
  --factory-artifact acceptance.json \
  --factory-attestation acceptance.attestation.json \
  --factory-key-id commissioning-lab
```

Admission requires all of the following:

- the Decision Lab artifact passes its content-addressed digest/provenance checks;
- `fallback_evaluation.measured=true`;
- at least one System-2 fallback case actually executed;
- no Decision Lab field contains the synthetic contract-fixture markers;
- the AI Factory artifact passes its content digest check;
- no AI Factory evidence reference is synthetic;
- the AcceptanceAttestation verifies against the explicitly trusted key id.

This command intentionally distinguishes a **live-shaped contract test** from a
real live proof. Passing it with locally fabricated data does not establish that
Qwen or hardware actually ran. The retained GitHub Actions/model artifact and
controlled-lab commissioning provenance remain the evidence of execution.

## Current status

v0.1.0 release hardening with frozen provable-execution and acceptance-artifact contracts. See [docs/V0.1_FREEZE.md](docs/V0.1_FREEZE.md) and [docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md](docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md).

Implemented:

- manifest/binding planning
- provider registry and executor boundary
- apply + compensation semantics
- FrozenEvidence approval/resume path
- Fleet/Agent AuthorityEnvelope inventory and fail-closed drift admission
- replay-verifiable ReleaseEvidence
- evidence-bound StateTransition / Authorization / ExecutionFence protocol
- provider-neutral `TransitionPlan` with exact-plan authorization and execution fencing
- durable authority reservation across provider side-effect and crash/takeover windows
- provider-neutral, plan-native `ExecutionJournal`
- crash/lost-ACK and stale/concurrent authority falsification matrices
- out-of-process `IndependentExecutionProof` verification from canonical artifacts
- Kubernetes Deployment scale provider with generation/resourceVersion fencing
- independent Deployment + Pods + Events observation and OutcomeContract verification
- recovery modeled as a newly authorized StateTransition
- deterministic Policy Replay contract
- Kubernetes sandbox runtime client
- Temporal workflow runtime client
- kubectl and Temporal CLI transports
- deterministic GPU XID Golden Incident
- AgentOS v3.2 Binding / Policy Projection / Evidence Provenance / ToolContract schemas
- CapabilityIntent → Rego v1 compilation with real OPA allow/deny enforcement
- cross-repository policy-digest provenance proof through Temporal, Kubernetes sandbox replacement and Evidence replay
- MCP stdio Execution Contract proof with lost-ACK, timeout, partial-commit and compensation fault injection
- opt-in live smoke preflight
- `make demo` / `make smoke` developer workflow

Not yet claimed:

- a production multi-tenant controller
- HA control-plane deployment
- live end-to-end testing against every supported provider
- a new durable Agent runtime
- ownership of Temporal/Sandbox provider semantics
- production-grade secret, budget and tenancy backends

See [DEVELOPMENT.md](DEVELOPMENT.md) and [RELEASE_READINESS.md](RELEASE_READINESS.md).

## Non-goal

This project is not trying to replace Temporal, Restate, OpenAI Agents, Codex, MCP, Kubernetes Agent Sandbox or agent gateways. Its job is to make those systems governable as one release/evidence plane.
