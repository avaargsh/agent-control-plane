# GPU XID Golden Incident

The demo is an executable reference slice for the control-plane contract:

```text
GPU XID Alert
  -> Evidence Snapshot
  -> Decision requiring approval
  -> FrozenEvidence
  -> Approval wait
  -> Resume without live reread
  -> Sandbox / MCP / Harness / Temporal / Decision bindings
  -> Eval Gate
  -> Promote or Rollback
  -> sealed ReleaseEvidence
  -> Replay Verification
```

Run:

```bash
python examples/gpu_xid_golden_incident.py
```

Executors are intentionally in-memory in this reference demo. The control-plane invariants are real; provider side effects are not. A deployment integration can replace each executor independently without changing the frozen-evidence or release-evidence contracts.


## Five-repository contract fixture

Run:

```bash
python examples/gpu_xid_golden_incident.py
```

The example is explicitly labeled `synthetic-contract-fixture`. It now binds
the contracts produced by all five repositories into one replayable release
evidence record:

```text
cloud-agent-runtime identity
  Session / Run / Temporal / Sandbox replacement lineage
        |
agent-decision-lab decision-eval/v1
  dataset digest / measured-format fallback evidence
        |
gpu-compute-platform workload identity
  workload id / generation
        |
ai-factory-engineering
  AcceptanceArtifact + trusted AcceptanceAttestation
        |
agent-control-plane
  EvalGate -> ReleaseEvidence -> replay verification
```

The fixture also carries a policy digest, approval receipt, MCP diagnostic
receipt, and the canonical AgentRelease identity.

This is a contract/integration proof only. Its decision measurements, compute
refs and factory evidence are synthetic and must not be reported as a real Qwen,
Kubernetes/GPU, NCCL/RDMA, or hardware commissioning result. The live evidence
gaps remain tracked in issue #52.
