# Release verification

v0.1.0 has two release gates: a package/public-surface gate and a live
Kubernetes Acceptance Artifact gate.

## 1. Package and public compatibility

Run from a fresh Git clone:

```bash
make setup
make verify-release
make audit-history
```

`make verify-release` checks:

- `project.version == 0.1.0` and Apache-2.0 package metadata;
- the frozen v0.1 public compatibility snapshot;
- the complete pytest suite;
- the deterministic GPU XID synthetic contract fixture;
- the MCP execution fault proof;
- release wheel build;
- isolated wheel installation;
- packaged schema availability and CLI validation.

A successful run writes:

```text
.artifacts/release/verification.json
```

`make audit-history` scans the real Git history with gitleaks when available,
otherwise trufflehog. Absence of a history scanner is never treated as success.

The CI package gate also runs `scripts/fresh_clone_release_smoke.sh`, which
repeats the public contract check, tests, deterministic demo, MCP proof, and
wheel build in a clean local clone.

## 2. Live Kubernetes acceptance

The normative v0.1.0 provider acceptance proof is the real kind Deployment
`20 -> 30` StateTransition.

The complete artifact contract is:

- [V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md](V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md)
- [../release/v0.1-acceptance-artifact-contract.json](../release/v0.1-acceptance-artifact-contract.json)

Create or select a clean kind cluster:

```bash
kind create cluster --name agent-transition --wait 120s
export KUBE_CONTEXT=kind-agent-transition
```

Then run the full clean-clone rehearsal:

```bash
make fresh-clone-kind-release-rehearsal
```

That target clones the exact current commit into a temporary directory,
installs the project there, runs `make kind-transition-smoke`, verifies the
serialized proof in a new Python process, validates the complete Acceptance
Artifact Contract, and copies the verified artifacts to:

```text
release-evidence/v0.1-kubernetes/
```

The directory includes `release-evidence.json`, containing the exact source
commit, IndependentExecutionProof statement hash, and SHA-256 file digest for
every required release artifact.

## Equivalent commands inside a clean clone

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

The required files are:

```text
execution-attestation.json
independent-execution-proof.json
independent-execution-proof.json.sha256
execution-journal.db
execution-leases.db
work-context.db
summary.json
```

`execution-attestation.json` is retained for compatibility.
`IndependentExecutionProof/v1` is the normative completed-execution proof.

## Release decision

Create the v0.1.0 tag only when all of the following are true:

1. package/public-surface gate is green;
2. full-history scan is green;
3. clean-clone Kubernetes acceptance rehearsal is green;
4. Acceptance Artifact Contract verifier is green;
5. no open P0 correctness issue remains;
6. release notes describe the Golden Slice, artifact contract, and known
   limitations.

A provider ACK or `replicas=30` alone is not a release proof:

> Desired state reached does not imply operation ownership proven.


## 3. Published-release regression

After v0.1.0, maintenance changes must remain able to verify the **already
published** release proof rather than only proofs regenerated from the current
source tree.

Run:

```bash
bash scripts/verify_published_v01_release.sh
```

The check downloads the two public v0.1.0 GitHub Release assets, verifies their
pinned SHA-256 digests, confirms the acceptance artifact is bound to release
source commit `62da9f33035e29ec5d29c224a7d676a687141ac5`, and re-runs the current
IndependentExecutionProof and Acceptance Artifact verifiers against those
published bytes.

This is a maintenance compatibility gate. It does not create a new proof format
or widen the v0.1 product boundary.
