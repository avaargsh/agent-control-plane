# Developer workflow

## Fast local loop

```bash
make setup
make test
make demo
```

The demo is deterministic and uses in-memory provider execution. It writes its
replay-verifiable synthetic Golden Incident summary to:

```text
.artifacts/demo/release-evidence-summary.json
```

It is a composition/replay fixture, not the v0.1 live provider acceptance
proof.

## v0.1 public compatibility

```bash
make verify-v01-public-contract
```

The frozen surface is documented in
[`docs/V0.1_PUBLIC_COMPATIBILITY.md`](docs/V0.1_PUBLIC_COMPATIBILITY.md).

## Live v0.1 Kubernetes proof

With a kind cluster named `agent-transition`:

```bash
export KUBE_CONTEXT=kind-agent-transition
make kind-transition-smoke
make verify-v01-acceptance-artifacts
```

For the release-quality clean-clone path:

```bash
export KUBE_CONTEXT=kind-agent-transition
make fresh-clone-kind-release-rehearsal
```

The normative artifact contract is
[`docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md`](docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md).

## Broader optional integration smoke

The repository also retains Kubernetes Sandbox / Temporal runtime-client
preflight tooling:

```bash
export KUBE_CONTEXT=kind-agent-control-plane
export KUBE_NAMESPACE=agent-runtime
export TEMPORAL_ADDRESS=127.0.0.1:7233
make preflight
make smoke
```

This profile is separate from the v0.1.0 release Golden Slice. Preflight
success does not claim end-to-end provider acceptance.
