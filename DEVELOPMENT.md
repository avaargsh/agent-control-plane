# Developer workflow

```bash
make setup
make test
make demo
```

The demo is deterministic and uses in-memory provider execution. It writes its replay-verifiable Golden Incident summary to `.artifacts/demo/release-evidence-summary.json`.

For live integration:

```bash
export KUBE_CONTEXT=kind-agent-control-plane
export KUBE_NAMESPACE=agent-runtime
export TEMPORAL_ADDRESS=127.0.0.1:7233
make smoke
```

`make smoke` first runs the executable preflight. The opt-in integration tests remain separate so normal CI never implies a live Kubernetes or Temporal environment was exercised.
