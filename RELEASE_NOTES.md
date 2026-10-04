# v0.1.0 Release Notes

## Agent Control Plane v0.1.0

v0.1.0 freezes a thin, evidence-first control plane for authorizing and proving
provider state transitions. The release does **not** introduce another Agent
runtime, workflow engine, sandbox scheduler, or Kubernetes Task CRD.

The strongest release claim is the real Kubernetes Deployment `20 -> 30`
Golden Slice, not the synthetic integration demo.

## Golden Slice proof

The release reference path is:

```text
project fenced execution lease
  -> fresh Kubernetes observation
  -> Frozen Evidence / StateTransition
  -> policy + signed approval
  -> exact TransitionPlan
  -> plan authorization + execution fence
  -> durable PREPARED ExecutionAttempt
  -> Kubernetes side effect using that exact plan
  -> process-boundary reconciliation
  -> fresh independent provider observation
  -> DesiredStateReached = TRUE
  -> OperationOwnershipProven = TRUE
  -> VerificationReport
  -> IndependentExecutionProof
  -> fresh-process verification against trusted statement hash
```

The live path deliberately exercises the crash window between provider mutation
and terminal journal persistence. Reconciliation only accepts `APPLIED` when
provider state proves the exact durable operation identity.

**Desired state reached does not imply operation ownership proven.**

For Kubernetes, ownership is derived from the fresh target observation using
the exact operation id, action hash, transition hash, plan hash, and authority
reservation hash.

## Acceptance Artifact Contract

The v0.1.0 release artifact contract is documented in:

- [docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md](docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md)
- [release/v0.1-acceptance-artifact-contract.json](release/v0.1-acceptance-artifact-contract.json)

A successful Golden Slice must produce:

- `execution-attestation.json` — compatibility artifact;
- `independent-execution-proof.json` — normative completed-execution proof;
- `independent-execution-proof.json.sha256`;
- `execution-journal.db`;
- `execution-leases.db`;
- `work-context.db`;
- `summary.json`.

The clean-clone release rehearsal validates this complete set and writes
`release-evidence.json` containing the exact source commit, proof statement
hash, and SHA-256 digest of every required artifact.

## Public compatibility

The explicit v0.1.0 public surface is documented in:

- [docs/V0.1_PUBLIC_COMPATIBILITY.md](docs/V0.1_PUBLIC_COMPATIBILITY.md)
- [release/v0.1-public-contract.json](release/v0.1-public-contract.json)

The release gate checks:

- installed console-script names;
- documented CLI command names;
- top-level package exports;
- exported authority dataclass fields;
- manifest kind-to-schema mapping and packaged schema presence;
- `IndependentExecutionProof/v1` top-level fields, predicate identity, and
  statement version.

Internal provider-adapter APIs, coordinator classes, and SQLite table layouts
are not promoted to stable public APIs by v0.1.0.

## Other proven contracts retained in v0.1.0

- deterministic FrozenEvidence approval/resume behavior;
- replay-verifiable ReleaseEvidence;
- provider-neutral binding planning and execution boundaries;
- CapabilityIntent -> Rego v1 compilation with real OPA allow/deny proof;
- Run / Session / Temporal / Kubernetes sandbox provenance contracts;
- MCP stdio side-effect contract under lost ACK, duplicate retry, timeout,
  partial commit, and compensation faults;
- provider-neutral TransitionPlan proof across Kubernetes Deployment scale and
  GitHub pull-request merge;
- crash/lost-ACK and stale/concurrent authority falsification;
- durable authority reservation and terminal repair evidence.

The GPU XID Golden Incident remains a deterministic
`synthetic-contract-fixture`. It is useful for composition/replay testing, but
it is not the v0.1.0 live provider acceptance proof.

## Verification

Package/public-surface gate:

```bash
make setup
make verify-release
make audit-history
```

Live acceptance from a clean clone with a `kind-agent-transition` context:

```bash
export KUBE_CONTEXT=kind-agent-transition
make fresh-clone-kind-release-rehearsal
```

Or, inside a clean clone:

```bash
export KUBE_CONTEXT=kind-agent-transition
make kind-transition-smoke

python scripts/verify_execution_proof.py \
  .artifacts/kubernetes-transition/independent-execution-proof.json \
  --expected-hash-file \
  .artifacts/kubernetes-transition/independent-execution-proof.json.sha256

python scripts/verify_v01_acceptance_artifacts.py \
  .artifacts/kubernetes-transition
```

## Known limitations

- This is a reference control plane, not a production multi-tenant service.
- HA deployment, production tenant isolation, secret management, and
  quota/budget backends are intentionally incomplete.
- The release proves one real Kubernetes mutation shape; it does not claim live
  end-to-end validation for every provider.
- `ExecutionAttestation/v2` is retained for compatibility but is not sufficient
  by itself for release acceptance.
- The `.sha256` file is a trusted-digest input, not proof of signer identity.
  Production authenticity remains external, for example DSSE/Sigstore/KMS.
- The SQLite evidence databases are required release artifacts, but their table
  layouts are not stable public wire formats.
- Temporal/Restate, sandbox runtimes, MCP, model/harness semantics, and provider
  scheduling remain external systems with narrow bindings.

## Upgrade policy

v0.1.0 freezes the explicit public compatibility surface above. Other internal
implementation details may evolve before v1. Changes to the frozen proof wire
identity, manifest schema mapping, top-level exports, or documented CLI surface
must be versioned explicitly rather than changed silently.
