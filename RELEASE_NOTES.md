# v0.1.0 Release Notes

## Agent Control Plane v0.1.0

The first release candidate establishes a thin, provider-neutral release and evidence plane for Agent systems.

### Highlights

- Compile AgentRelease and provider bindings into an ordered execution plan.
- Freeze evidence at approval boundaries and resume against the approved snapshot.
- Keep Kubernetes sandbox and Temporal workflow identities in one provenance chain.
- Execute through provider-neutral executor/runtime-client contracts.
- Seal replay-verifiable ReleaseEvidence after evaluation gates.
- Run the GPU XID Golden Incident deterministically with `make demo`.
- Keep live Kubernetes/Temporal validation explicitly opt-in through preflight/smoke tooling.

### Important boundaries

This release is not a new durable Agent runtime, a production multi-tenant controller, or a replacement for Temporal, Kubernetes sandbox runtimes, MCP, harnesses or gateways. Live end-to-end provider validation remains environment-dependent.

### Upgrade policy

v0.x provider, binding and release contracts may evolve. Changes to identity, approval, evidence or replay semantics should be treated as compatibility-sensitive.
