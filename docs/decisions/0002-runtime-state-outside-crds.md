# ADR-0002: Keep high-frequency runtime state outside CRDs

- Status: Accepted

## Decision

Kubernetes-style declarative objects represent desired state only.

High-frequency runtime state is stored in fit-for-purpose systems:

- PostgreSQL or another transactional store for session/run metadata,
- object storage for artifacts and large evidence,
- OpenTelemetry-compatible backends for traces and metrics.

## Rationale

Agent runs can produce large, rapidly changing state. Treating the Kubernetes API as an event or trajectory database creates unnecessary write pressure and couples application semantics to cluster control-plane health.
