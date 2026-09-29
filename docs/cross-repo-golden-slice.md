# Cross-Repo Golden Slice v0.1

This is the integration contract across the portfolio. It deliberately does **not** create another runtime.

## Flow

```text
AI Factory SLO violation
  -> Agentic AIOps freezes EvidenceBundle
  -> Decision Gateway returns calibrated decision
  -> Agent Control Plane evaluates policy / approval / eval gates
  -> Temporal owns durable continuation
  -> Agent Sandbox owns ephemeral execution
  -> AI Factory verifies stable SLO recovery
  -> ReleaseEvidence records the outcome for replay
```

## Canonical identity chain

Every boundary must propagate stable references rather than copy ownership:

- `run_id`: canonical business/agent run identity.
- `workflow_id`: Temporal continuation identity.
- `sandbox_id`: replaceable execution environment identity.
- `evidence_id`: immutable/frozen observation bundle.
- `decision_id`: calibrated decision result and confidence.
- `release_id`: desired-state/release identity controlled by this plane.
- `trace_id`: observability correlation identity; it is not business identity.

A sandbox may die, suspend, resume, or be replaced without changing `run_id`. A workflow may attach to an existing execution without creating a new canonical run.

## Ownership

| Concern | Owner |
|---|---|
| SLO measurement and recovery verification | ai-factory-engineering |
| Incident evidence freeze and replay metrics | agentic-aiops |
| Candidate decision, calibration, confidence gate | agent-decision-lab |
| Desired state, bindings, policy, approval, release/eval gate | agent-control-plane |
| Durable continuation | Temporal provider |
| Ephemeral execution lifecycle | Agent Sandbox provider |
| Run/session identity and provider attachment | cloud-agent-runtime |

## Acceptance contract

The slice is accepted only when an automated test proves:

1. a degraded SLO produces immutable evidence;
2. the decision references exactly that evidence digest;
3. approval/resume performs zero live rereads of the pre-approval evidence;
4. `run_id` survives workflow attach and sandbox replacement;
5. recovery is accepted only after a stable SLO window;
6. all execution receipts and rollback receipts are replayable;
7. an eval-gate failure blocks or rolls back promotion deterministically.

## Non-goals

- No new Durable Agent Run runtime.
- No orchestration semantics duplicated from Temporal/Restate.
- No sandbox lifecycle duplicated from Kubernetes Agent Sandbox.
- No model chain-of-thought used as audit evidence.
- No high-frequency runtime state stored in Kubernetes CRDs.

## Next implementation seam

Define a small versioned `GoldenSliceEnvelope` carrying the canonical IDs plus evidence/decision/release references. Each repository may adapt its native object to this envelope, but the envelope must not become a new execution engine.
