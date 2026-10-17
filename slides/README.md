# AgentLedger presentations

There are **two presentations**, both included with the portable project sources.

| Source | Purpose | Talk length |
|---|---|---|
| `judge.md` | Seven-slide judge pitch: product, four-click demo, failure semantics and what remains unverified | ~3 minutes |
| `slides.md` | Engineering appendix: SDK, ABI/identity, evidence, test gates and roadmap | ~10–15 minutes |

## View the concise pitch

```bash
cd slides
npm ci --ignore-scripts --no-audit --no-fund
npm run dev:judge
# or produce a hostable bundle:
npm run build:judge
# generated: slides/dist-judge/index.html
```

## Export a presentation or PDF

```bash
npm run export:judge:pptx
npm run export:judge:pdf
```

Slidev uses Playwright/Chromium for browser exports. If the exporter
reports a missing browser, install the browser tooling used by Slidev
(or use the project's standard Chromium automation environment).
The PPTX exported by Slidev is normally image-backed, whereas the
separately delivered PPTX preview prepared for this pitch uses native
PowerPoint text and shapes and is editable.

## Live demonstration handoff

1. Open `apps/pocket` Web (or the original Verification Studio).
2. In Pocket, select **▶ Start 3-minute guided demo**. The progress bar
   and next/back buttons follow the same sequence as slide 4:
   Created → Recorder Paid claim → Evidence / Verify → Lost ACK.
3. On the Evidence step, press **Verify SHA-256 locally**. A matching
   digest is byte-consistency only, not independent payment acceptance.
4. Show `PaymentUnknown` and **NO RECHARGE** in the Lost ACK step.
5. Export `summary.json` in Pocket Journal/desktop Inspector.
6. If network access is available, separately show the previously
   recorded public Monad Testnet **Created** task through the read-only
   verifier. This is not the same as the simulated Paid/Proven story.

**Never claim:** actual public Testnet USDC settlement, finality,
reviewed deployment, independently correct Agent execution, or an iOS
native release. The reviewed-deployment registry remains empty.

The old `slides.md` is retained for judge Q&A; do not present all of
its pages before the demo. Presenter notes in `judge.md` include
approximate timings.

## Manual hackathon submission

Use [the MOJO handoff checklist](../docs/monad-blitz-submission.md) and `npm run submit:check` before copying a reviewed version to the final repository. Judge slides intentionally do not display any development-repository or target-submission repository URL. Neither presentation claims real Testnet USDC payment acceptance.

## Handoff from the 7-slide pitch

The **Clickable Demo** slide contains two loopback-only links: Pocket Web on `http://127.0.0.1:8081` and the six-step offline Studio guided demo on `http://127.0.0.1:4322/demo`. Start those services on the **same computer** as the presentation browser. These are development-only local navigation links, not deployed sites. If demonstrating on a separate device, use approved port forwarding and open the corresponding service directly; do not treat a failed `127.0.0.1` link as a Testnet failure.

Pocket's fourth step now ends in **Export guided summary.json**, with the payment claim still `PaymentUnknown`; Studio displays a separate seventh, **optional** Testnet read. Both presentation surfaces use a restrained navy/blue visual system and retain their source labels.
