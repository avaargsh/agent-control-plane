# AgentLedger — Slidev hackathon pitch (13 slides)

This deck is authored in [Slidev](https://github.com/slidevjs/slidev)
and uses the real AgentLedger Phase 0 product and acceptance boundaries.

## Slides

1. Pay. Execute. Prove. · Project thesis
2. Agent economy's missing proofs · Payment / execution / evidence
3. State is not proof · Fact separation
4. Pocket as the single end-user app · Mobile task-to-proof journey
5. Four-step desired path · Created / Settlement / ACP / Evidence
6. AgentLedger alongside ACP · Read-only layer architecture
7. Verification Studio · Adversarial test paths, local Anvil
8. Offline-first readiness.json · No-wallet preflight
9. PaymentUnknown, cross-Ledger and deployment fail-closed
10. Monad parallel EVM + x402 context · No performance claims
11. Two-minute interactive demo · Pocket first, Studio for reviewers
12. Current local evidence versus public Testnet blockers
13. Project close / next gate

## Run

Requires Node 22+, Chromium for exporting, Chinese font support.

```bash
cd experiments/agentledger/pitch/slidev
npm install
npm run dev             # Live presenter UI
npm run build           # Static interactive browser deck → dist/
npm run export:pptx     # Slidev-generated slideshow-compatible PPTX
npm run export:pdf
npm run export:editable # Optional native-shape PPTX; CSS-heavy slides may fall back to images
```

Open the Slidev presenter mode from the browser UI to use the
per-slide speaking notes. The PPTX slides generated using
`--format pptx` are rendered visual images rather than editable
PowerPoint text; the Markdown source remains the canonical editable
format. The `pptx-editable` export is only best-effort.

## Important claim boundaries

- **Local done:** Pocket Expo Web UX, on-device canonical SHA-256,
  Studio negative cases, Foundry + Anvil *local* lifecycle, offline
  readiness.json and simulated summary.json.
- **Not accepted:** No independently reviewed Monad Testnet deployment,
  no real paid Testnet USDC settlement acceptance, no irrevocable
  finality claim, and no independent AgentRun execution proof.
- Ledger's `Paid` is a trusted Recorder *attestation*, not itself
  independent USDC verification. `PaymentUnknown` prohibits recharge.
- Evidence digest match is byte equality with a hash anchor, not proof
  that the Agent executed correctly.
- Pocket is the sole end-user application. Studio is for engineers;
  the Node SDK `observe/verify/reconcile` remains private and read-only.

Project sources:
- [AgentLedger README](../../README.md)
- [Onsite handoff](../../docs/onsite-handoff.md)
- [Verification Studio acceptance](../../docs/verification-studio-demo-acceptance.md)
- [Reviewed deployments registry](../../phase0/reviewed-testnet-deployments.json)

This deck is part of the experiment, not a production SLA or an
acceptance certificate. There are no wallet keys or transaction
signing steps in the presentation.

## Build artifacts

The separate GitHub Action `AgentLedger Slidev Pitch` exports:
`AgentLedger-Pitch.pptx`, `AgentLedger-Pitch.pdf`,
`AgentLedger-Slidev-Source.zip` and, if supported by the current
Slidev CLI, `AgentLedger-Pitch-editable.pptx`.
