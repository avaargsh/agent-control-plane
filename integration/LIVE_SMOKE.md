# Live Smoke Integration Profile

This profile is intentionally opt-in. Unit CI must not require a Kubernetes cluster or Temporal server.

## Prerequisites

- a local Kubernetes cluster (kind or minikube)
- a Sandbox-compatible CRD installed in the cluster
- a Temporal development server
- Python transports implementing the existing `KubernetesApi` and `TemporalApi` protocols

## Contract under test

```text
Frozen approved incident
  -> KubernetesSandboxExecutor -> live Sandbox UID/resourceVersion
  -> TemporalWorkflowExecutor  -> live workflowId/runId
  -> Eval Gate
  -> sealed ReleaseEvidence
  -> replay verification
```

## Environment

```bash
export ACP_LIVE_SMOKE=1
export ACP_KUBE_CONTEXT=kind-agent-control-plane
export ACP_KUBE_NAMESPACE=agent-runtime
export ACP_TEMPORAL_ADDRESS=127.0.0.1:7233
```

A live smoke test must skip unless `ACP_LIVE_SMOKE=1`. This keeps ordinary CI deterministic while making the external-system boundary explicit.

## Pass criteria

1. Sandbox create returns a real Kubernetes UID and resourceVersion.
2. Reconcile retry does not create a second sandbox.
3. Temporal start returns a real workflowId/runId and retry attaches to the same workflow identity.
4. ReleaseEvidence contains both runtime identities.
5. Replay verification succeeds.
6. Compensation can terminate the workflow and delete the sandbox.
