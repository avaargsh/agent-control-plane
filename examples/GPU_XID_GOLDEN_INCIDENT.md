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


## Inject externally produced artifacts

The contract fixture does not need to be rewritten when real Decision Lab and AI
Factory outputs become available. Use the external-artifact runner:

```bash
export AI_FACTORY_ATTESTATION_SECRET='...'

python examples/gpu_xid_external_artifacts.py \
  --decision-eval /path/to/decision-eval.json \
  --factory-artifact /path/to/acceptance.json \
  --factory-attestation /path/to/acceptance.attestation.json \
  --integration-envelope examples/gpu-xid-external-envelope.example.json \
  --factory-key-id commissioning-lab \
  --output /tmp/gpu-xid-release-evidence.json
```

The runner verifies and binds the supplied content-addressed Decision Lab artifact
and trusted AI Factory attestation into the same release/eval/replay path.

Its output is deliberately labeled:

```text
fixtureMode = external-artifact-integration
providerExecutionMode = in-memory-control-plane-contract
```

This means externally measured evidence may be real while provider execution in
this runner is still the deterministic Control Plane contract executor. A live
five-repository proof must replace the runtime/compute identities and receipts
with the actual provider outputs rather than relabeling this integration runner.
