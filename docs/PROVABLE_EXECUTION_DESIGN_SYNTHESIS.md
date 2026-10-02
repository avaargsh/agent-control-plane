# Provable Execution Design Synthesis

## Purpose

This document does **not** propose integrating vendor stacks for the sake of
integration.

The project should learn from mature control planes, durable runtimes,
authorization engines, progressive-delivery systems, and attestation
frameworks, then absorb only the mechanisms that strengthen the core invariant:

> An approved real-world state transition must remain bound to the exact
> observation, authority, operation ownership, provider outcome, and
> independently verified postcondition that justified it.

Agent frameworks are only one source of proposals.

## Research set

The closest architectural analogues are not primarily agent frameworks.

| System | Mature idea to learn from | Do not copy |
| --- | --- | --- |
| Kubernetes | optimistic concurrency, generation, conditions, reconcile | Kubernetes-specific API machinery |
| Crossplane | Observe/Create/Update/Delete provider boundary, external-state reconciliation | generic cloud resource control plane |
| Terraform / OpenTofu | immutable plan, approval of exact plan, state locking | IaC state engine |
| Temporal | event history, durable continuation, activity retry boundary | workflow runtime |
| Restate | journal as ground truth, single-writer keyed execution, durable operation identity | durable execution runtime |
| Dogwood | history-aware temporal authorization | policy language / policy log |
| Cedar / OPA | authorization separated from business logic | policy engine |
| Argo Rollouts | verification is a first-class phase, promotion/rollback depends on evidence | rollout controller |
| in-toto / SLSA / Sigstore | subject + predicate + signer identity + verifiable attestation | software-supply-chain-specific workflow |

Microsoft Agent Framework, Google AX, AWS Strands/AgentCore, ADK, and similar
agent stacks are useful compatibility targets, but they are not the design
center of this protocol.

---

## 1. Learn from Kubernetes: status must say what generation it proves

Kubernetes separates desired state from observed status. Status conditions can
carry an `observedGeneration` so a reader can tell whether a status statement
was produced against the current desired generation.

The protocol should adopt the same discipline.

### Add explicit observation identity

Every observation used for approval or verification should expose:

```text
ObservationSnapshot
  subject
  observed_version
  observed_at
  canonical_state
  evidence_digest
  observer_identity
```

Provider-specific versions may be a Kubernetes `resourceVersion`, GitHub HEAD
SHA, cloud ETag, database schema revision, or another opaque token.

The core must compare identity/equality according to provider semantics. It
must not assume that every version is numeric or globally ordered.

### Verification conditions

Do not reduce outcome verification to a single boolean.

Use Kubernetes-style conditions:

```text
VerificationCondition
  type
  status = TRUE | FALSE | UNKNOWN
  reason
  message
  observed_transition_generation
  evidence_digest
```

Examples:

- `DesiredStateReached`
- `HealthSatisfied`
- `OperationOwnershipProven`
- `IndependentObservationFresh`

This gives UNKNOWN an explicit and durable meaning instead of treating missing
proof as failure or success.

---

## 2. Learn from Crossplane: provider contracts begin with Observe

Crossplane's managed-resource model makes external observation a first-class
provider operation and keeps provider-specific semantics behind a narrow
boundary.

The long-term mutation-provider contract should be conceptually:

```text
observe(subject) -> ObservationSnapshot

plan(before, desired) -> TransitionPlan

apply(plan, execution_context) -> ProviderReceipt | transport failure

reconcile(attempt, fresh_observation) -> ReconciliationResult
```

Not every provider must expose literal methods with these names, but every
implementation must be able to satisfy these semantics.

### Important consequence

`observe()` is not a helper around `apply()`.

The observation used after a side effect should be independently acquired and
must not reuse an execution client's optimistic cache as proof.

### Borrow management scope, not Crossplane itself

Crossplane distinguishes Observe/Create/Update/Delete management policies. The
protocol can borrow the idea of an **authority scope**:

```text
AuthorityScope
  operations: [OBSERVE, UPDATE]
  subject_scope
  expires_at
```

This should describe what the authorization permits without turning the
project into another resource-management platform.

---

## 3. Learn from Terraform/OpenTofu: approve the exact plan, not an intention

Terraform/OpenTofu make a strong distinction between a speculative plan and a
saved plan that can later be applied. State locking prevents concurrent writers.

The corresponding primitive here should be a sealed `TransitionPlan`.

```text
ObservationSnapshot
        |
        v
TransitionPlan
  subject
  before_digest
  desired_digest
  provider_preconditions
  provider_operation_digest
  plan_digest
        |
        v
Approval / Policy
must bind plan_digest
```

The human or policy engine should approve **the exact plan digest**, not only
"scale deployment" or "merge PR".

Immediately before the provider side effect, the executor revalidates the
provider precondition.

### Reservation IDs are lock nonces

OpenTofu's force-unlock requires the specific lock ID. Borrow that safety
property.

An authority reservation may be released or repaired only by proof that names
the exact reservation ID/hash. Never support a generic "unlock this resource"
operation.

---

## 4. Learn from Temporal and Restate: continuation durability is not effect correctness

Temporal's event history and Restate's journal demonstrate the right place to
solve continuation/retry durability.

Agent Control Plane should not reproduce a durable workflow engine.

However, two design lessons belong in the protocol.

### Journal before side effect

The operation identity and all authority bindings must become durable before
the external mutation can happen.

```text
durable PREPARED
     |
     v
external side effect
     |
     +--> receipt
     |
     +--> timeout / crash / lost ACK
```

### External effects remain special

Restate explicitly notes that a side effect can execute more than once if a
failure happens after the external operation but before its result becomes
durable. Temporal similarly expects external operations to tolerate retries or
use stable IDs/idempotency.

That validates keeping a provider-side ambiguity protocol:

```text
PREPARED
  -> COMMITTED
  -> ABORTED
  -> UNKNOWN
```

`UNKNOWN` means:

- the runtime may continue/recover;
- authority is still frozen;
- the side effect MUST NOT simply be retried;
- fresh provider reality must be reconciled first.

This is a core project differentiator.

### Do not conflate workflow compensation with execution repair

A compensation is a new real-world state transition.

It should normally require new observation, policy/approval and authorization,
rather than being silently executed as rollback code because a workflow failed.

---

## 5. Learn from Dogwood, Cedar and OPA: policy is an input, not the control plane

Cedar's core model is principal/action/resource/context -> allow/deny. Dogwood
extends policy decisions over prior action history and outcomes.

The protocol should not own another general-purpose policy language.

It should standardize a verifiable decision envelope:

```text
PolicyDecisionEnvelope
  engine_identity
  policy_set_digest
  policy_input_digest
  decision
  determining_policy_refs
  evaluated_at
  decision_digest
  optional_history_ref
```

Authorization binds the decision envelope digest.

The implementation may use deterministic local policy in tests, Cedar,
Dogwood, OPA, an enterprise policy service, or a custom authorizer. The protocol
does not need a hard dependency on any of them.

### Why preserve determining policy references?

It makes later replay answer two different questions:

1. Was this exact decision the one used at authorization time?
2. Under which policy material was it made?

Re-evaluating today's policy is not sufficient to prove yesterday's authority.

---

## 6. Learn from Argo Rollouts: execution success and outcome success are different

A provider returning HTTP 200 or a tool returning "success" does not prove the
approved business outcome.

Argo Rollouts treats analysis and verification as first-class steps that can
block promotion or cause rollback.

Adopt the same separation:

```text
ProviderReceipt
      !=
VerificationReport
```

A generic verification plan may include multiple gates:

```text
VerificationPlan
  - Convergence gate
  - Health gate
  - Domain invariant gate
  - Optional external metric gate
```

The first provider slice may stay simple. The model should still allow
`TRUE/FALSE/UNKNOWN` conditions rather than one provider-specific success flag.

Rollback is not a special hidden path. It is another authorized transition.

---

## 7. Learn from in-toto / SLSA / Sigstore: attestations should be independently verifiable

The current custom execution attestation should move toward the in-toto
attestation model:

```text
Statement
  subject
  predicateType
  predicate
```

For this project:

```text
subject:
  digest of canonical ExecutionRecord

predicateType:
  state-transition-execution/v1

predicate:
  subject identity
  before observation digest
  transition plan digest
  policy decision digest
  approval / authorization digest
  reservation / fence identity
  attempt / operation identity
  provider receipt digest
  reconciliation result
  after observation digest
  verification report digest
  executor identity
  timestamps
```

The protocol should remain usable unsigned in local tests, but the schema should
be designed so a deployment can wrap the statement in DSSE / Sigstore / KMS
signatures without changing execution semantics.

Important distinction:

```text
hash integrity != trusted provenance
signature validity != outcome correctness
outcome proof != policy authorization
```

The attestation binds all three; it does not collapse them.

---

## 8. The synthesized protocol

The resulting architecture is smaller than the previous AgentOS shape.

```text
Proposal Source
 Agent / Human / GitOps / Workflow
             |
             v
     Fresh Observation
             |
             v
      TransitionPlan
   immutable + digest-bound
             |
      +------+------+
      |             |
      v             v
 PolicyDecision   Approval
      |             |
      +------+------+
             v
     AuthorizationProof
             |
             v
 AuthorityGeneration / Reservation
             |
             v
       ExecutionFence
             |
             v
        PREPARED
             |
             v
      Provider Side Effect
             |
      +------+------+ 
      |             |
   Receipt       Ambiguous
      |             |
      +------v------+
             |
        Reconciliation
             |
             v
    Independent Observation
             |
             v
      VerificationReport
             |
             v
 Execution Attestation
    in-toto-shaped
```

### Product center

The center is not the agent and not the workflow.

It is:

> **plan-bound, authority-fenced, reconciled and independently attested external
> state transition execution.**

---

## 9. What to keep from #129-#133

Keep and generalize:

- authority generation separated from non-authoritative context revision;
- exact authority reservation identity;
- lease/epoch fencing where provider semantics need single-writer ownership;
- PREPARED-before-side-effect durability;
- COMMITTED / ABORTED / UNKNOWN outcome semantics;
- fail-closed UNKNOWN authority retention;
- provider-state reconciliation after lost ACK;
- terminal-proof-based reservation repair;
- fresh post-execution observation;
- execution attestation.

Change:

- make reconciliation/provider ownership generic rather than Kubernetes-shaped;
- move from ad-hoc before/desired payloads toward a sealed `TransitionPlan`;
- model verification as conditions/report rather than a single success result;
- make attestation in-toto-shaped and optionally signable;
- reduce policy implementation ownership to `PolicyDecisionEnvelope`.

---

## 10. What not to build

Do not build these unless a concrete falsification experiment proves they are
required for the core invariant:

- agent framework;
- durable agent runtime;
- workflow engine;
- sandbox scheduler;
- agent registry/fabric;
- generic policy language;
- generic policy history store;
- generic approval UI;
- generic cloud control plane;
- infrastructure-as-code state engine;
- generic rollout controller;
- proprietary attestation/signing system.

Mature systems already own those problems.

---

## 11. Next experiments — not integrations

### Experiment A — Generic provider contract

Refactor only enough to prove one protocol can express both:

1. Kubernetes Deployment replicas 20 -> 30
2. GitHub PR open@HEAD -> merged@same HEAD

Success criterion:

- no provider-specific field leaks into the core transition/journal schema;
- both use the same TransitionPlan, AuthorizationProof, attempt states and
  VerificationReport model.

### Experiment B — Crash matrix

Seed failures at every boundary:

```text
before PREPARED
after PREPARED
after provider accepted mutation
before provider reply
after reply / before journal commit
after COMMITTED / before reservation release
during verification
```

For each failure, prove exactly one of:

- safely retryable;
- safely reconciled;
- explicitly UNKNOWN and blocked.

No silent retry is acceptable for an ambiguous provider side effect.

### Experiment C — Concurrency matrix

Inject:

- subject version drift;
- higher authority generation;
- lease epoch replacement;
- second writer;
- stale approval;
- provider-side mutation by an unrelated actor.

Prove stale authority cannot mutate and ambiguous ownership cannot be released.

### Experiment D — Verifier independence

Generate an execution attestation, then verify it in a separate process from
only:

- canonical artifacts;
- trusted signer/policy roots where configured;
- provider evidence references.

The verifier should not need the original agent session or hidden reasoning.

---

## 12. Kill criteria

Reduce the project to glue or stop it if an existing system demonstrates, in one
coherent provider-neutral contract:

1. immutable observation-bound execution plans;
2. approval/authorization bound to the exact plan digest;
3. stale/concurrent provider mutation fencing;
4. durable operation ownership before side effects;
5. explicit ambiguous/lost-ACK outcome state;
6. provider-state reconciliation proving applied vs not-applied ownership;
7. fail-closed retention of authority while outcome is unknown;
8. independent postcondition verification;
9. replay-verifiable, independently verifiable execution attestation.

Until then, research and implementation should deepen these guarantees rather
than expand horizontally into Agent infrastructure.
