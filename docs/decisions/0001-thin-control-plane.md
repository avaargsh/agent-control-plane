# ADR-0001: Keep the control plane thin

- Status: Accepted

## Context

Agent platforms are accumulating overlapping implementations of loops, workflows, sandboxes, tools, memory and provider-specific run state.

Reimplementing all of these in a new control plane creates lock-in and makes provider evolution expensive.

## Decision

The Agent Control Plane owns portable desired state and governance:

- bundle and release identity,
- provider bindings,
- policy and approval,
- placement and rollout,
- context projection,
- evidence references,
- evaluation/release gates.

Execution semantics remain with specialized providers.

## Consequences

Positive:

- providers can evolve independently,
- the control plane remains smaller,
- migrations can preserve canonical session/release identity,
- policy and evidence stay consistent across runtimes.

Trade-offs:

- adapters are unavoidable,
- not every provider feature maps to a portable core,
- provider extensions are needed for non-portable capabilities.
