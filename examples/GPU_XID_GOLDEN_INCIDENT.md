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
