# Agent Authority Envelope and Drift Admission

The control plane separates two authority layers:

1. **Deployment authority** — the maximum authority an Agent is allowed to hold across a Fleet/Release/Runtime combination.
2. **Incident authority** — a one-operation authorization frozen to exact evidence, an exact approval ID and a bounded write.

The first layer is represented by `AgentAuthorityEnvelope`. The second remains in the runtime/AIOps path. They are linked by the deployment `authorityDigest`, but they are not interchangeable.

## Deployment path

```text
Desired AgentRelease
      +
AgentAuthorityEnvelope
      |
      v
Fleet / Agent authority inventory
      |
      v
deployed envelope  --diff-->  proposed envelope
      |
      v
deterministic drift admission
      |
      +--> ADMIT: no expansion / only tightening
      |
      +--> DENY: authority expansion, runtime-boundary change,
                 weaker approval/evidence, or larger write budget
      |
      v
AgentRelease freezes authorityRef + authorityDigest
      |
      v
Runtime / incident execution
      |
      v
exact approval_id + evidence_digest + bounded write
```

## What is inventory state?

The inventory is derived state, not another desired-state CRD. It groups admitted envelopes as:

```text
Fleet
  -> Agent
      -> deployment / release
          -> authorityDigest
          -> runtimeRefs
          -> grantCount
```

This keeps Kubernetes-style desired state small while still making Fleet-wide authority queryable.

## Default admission rules

The reference admission function is intentionally conservative:

- adding a grant is `DENY / AUTHORITY_EXPANSION`
- changing runtime refs is `DENY / RUNTIME_BOUNDARY_CHANGED`
- weakening `human-exact -> policy -> none` is denied
- disabling required evidence is denied
- increasing `maxOperationsPerRun` is denied
- removing grants or reducing the write budget is admitted
- changing only the release version with identical authority keeps the same authority digest

A deny means the deployment needs an explicit authority-review path. Future OPA integration can express organization-specific exceptions without moving policy into Temporal or the sandbox runtime.

## Why the digest excludes release metadata

`authorityDigest` hashes authority semantics: Fleet/Agent identity, runtime enforcement boundaries, grants and constraints. It intentionally excludes `releaseRef` and envelope metadata so a code-only release does not look like authority drift.

`AgentRelease` records both `authorityRef` and `authorityDigest`. Runtime evidence can therefore prove exactly which deployment-time authority contract was active when an incident approval was consumed.
