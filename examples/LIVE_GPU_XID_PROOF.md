# Live GPU XID proof

This is the final substitution step after the synthetic five-repository contract
fixture has passed.

## Required retained inputs

1. `decision-eval.json` from the model-enabled Decision Lab workflow.
2. `acceptance.json` from a controlled AI Factory commissioning run.
3. `acceptance.attestation.json` created from that exact artifact.
4. A live Golden Slice evidence JSON derived from
   `examples/live-gpu-xid-evidence.template.json`.

The evidence JSON must contain the actual AgentRelease, Session/Run, Temporal,
Sandbox, Compute Workload and MCP receipt identities. Do not copy the synthetic
fixture identifiers.

## Preflight

```bash
export AI_FACTORY_ATTESTATION_SECRET='...'

agent-control-plane live-proof-verify \
  --decision-artifact decision-eval.json \
  --factory-artifact acceptance.json \
  --factory-attestation acceptance.attestation.json \
  --factory-key-id commissioning-lab
```

## Produce the final ReleaseEvidence

```bash
python examples/run_live_gpu_xid_proof.py \
  --decision-artifact decision-eval.json \
  --factory-artifact acceptance.json \
  --factory-attestation acceptance.attestation.json \
  --golden-slice-evidence live-gpu-xid-evidence.json \
  --factory-key-id commissioning-lab \
  --output release-evidence.live.json
```

The runner fails closed when the model artifact is synthetic, the System-2 path
did not actually execute, the factory evidence is synthetic, the attestation is
not trusted, required live identities/receipts are missing, the release does not
promote, or replay verification fails.

A successful command is the integration proof. Keep the original workflow and
commissioning provenance beside the resulting `release-evidence.live.json`;
the JSON contract alone cannot prove that external execution actually occurred.
