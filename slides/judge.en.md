---
theme: default
title: AgentLedger · 3-minute judge pitch (English)
layout: cover
colorSchema: dark
class: text-center
info: |
  Hackathon demo: local simulation and public read-only observations are separate.
  No verified real USDC payment acceptance is claimed.
---

<div class="al-pitch-cover">
  <div class="al-pitch-kicker">AGENTLEDGER · MONAD × x402 · JUDGE PITCH</div>
  <h1>Pay → Execute → <span>Prove.</span></h1>
  <p>Agents can charge for tasks.<br>But <strong>who can verify the payment, execution and evidence</strong> independently?</p>
  <div class="al-pitch-tags">
    <span>01 · Task</span><span>02 · PaymentUnknown</span><span>03 · Evidence</span>
  </div>
  <div class="al-pitch-flow">3-minute pitch <b>→</b> 4-step Pocket guide <b>→</b> 6-step offline Studio + optional Testnet read</div>
  <div class="al-pitch-warning">Prototype · Public task observed read-only · Real USDC payment acceptance not completed</div>
</div>

<!-- 0:00–0:20: Problem and product, not framework comparisons. -->

---
layout: default
---

<div class="al-pitch-kicker">01 / THE PROBLEM</div>
<div class="al-pitch-title">An Agent's “success”<br><em>is not a verified fact.</em></div>
<div class="al-pitch-three">
 <div class="al-pitch-tile"><div class="al-pitch-index">01 · PAYMENT</div><h2>Paid?</h2><p>When an HTTP acknowledgment is lost, was the charge settled—or did the response just disappear?</p><b>Lost ACK ≠ Unpaid</b></div>
 <div class="al-pitch-tile"><div class="al-pitch-index">02 · EXECUTION</div><h2>Done?</h2><p>A recorder or Agent claiming success cannot establish who owned the operation.</p><b>Claim ≠ Proof</b></div>
 <div class="al-pitch-tile"><div class="al-pitch-index">03 · EVIDENCE</div><h2>Verified?</h2><p>Without the original inputs, outputs and unambiguous hashes, how can another party check the result?</p><b>Hash match ≠ Correctness</b></div>
</div>
<div class="al-pitch-bottom">Payment, execution and evidence must be verified separately—not combined into one green checkmark.</div>

<!-- 0:20–0:45: Unknown is a valid outcome, not an error. -->

---
layout: default
---

<div class="al-pitch-kicker">02 / THE PRODUCT</div>
<div class="al-pitch-title">One experience.<br><em>Independent facts.</em></div>
<div class="al-pitch-product">
 <div class="al-pitch-phone">
  <div class="al-pitch-ph-top"><strong>agentledger.</strong><span>READ ONLY</span></div>
  <div class="al-pitch-ph-hero">Your work.<br><strong>Verifiable.</strong></div>
  <div class="al-pitch-ph-row"><span>Task ID / descriptionHash</span><b>Created</b></div>
  <div class="al-pitch-ph-row"><span>Recorder declaration</span><b class="al-amber">Paid claim</b></div>
  <div class="al-pitch-ph-row"><span>USDC settlement</span><b class="al-amber">PaymentUnknown</b></div>
  <div class="al-pitch-ph-row"><span>Evidence SHA-256</span><b class="al-mint">Verify bytes</b></div>
  <div class="al-pitch-ph-tabs">TASKS　·　PAYMENT　·　EVIDENCE　·　JOURNAL</div>
 </div>
 <div class="al-pitch-right">
  <div class="al-pitch-card"><b>AgentLedger Pocket</b><p>A mobile task-to-evidence journey, with a desktop Inspector and exportable summary.json.</p></div>
  <div class="al-pitch-card"><b>Verification Studio + SDK</b><p>Read-only <code>observe / verify / reconcile</code> with strict chain, contract, task and digest identity checks.</p></div>
  <div class="al-pitch-card green"><b>No hidden payment authority</b><p>The presentation reads facts and hashes bytes locally. It neither charges wallets nor runs Agents.</p></div>
 </div>
</div>
<div class="al-pitch-bottom">The Ledger adds settlement observation and evidence anchoring. It does not become a second Agent runtime.</div>

<!-- 0:45–1:15: Open the actual Pocket Web app. -->

---
layout: default
---

<div class="al-pitch-kicker">03 / CLICKABLE DEMO</div>
<div class="al-pitch-title">Four steps.<em> Reproducible live.</em></div>
<div class="al-pitch-journey">
 <div class="al-pitch-stage"><i>01</i><strong>Created</strong><p>Task ID, descriptionHash and chain ID 10143</p></div>
 <div class="al-pitch-stage"><i>02</i><strong>Paid claim</strong><p>Recorder declares Paid; settlement remains unverified</p></div>
 <div class="al-pitch-stage"><i>03</i><strong>Evidence</strong><p>Original files and local SHA-256 verification</p></div>
 <div class="al-pitch-stage"><i>04</i><strong>Lost ACK</strong><p>Uncertain outcome → never recharge</p></div>
</div>
<div class="al-pitch-demo-bar"><span>Pocket: Start guide → Next / Back → Lost ACK → <b>summary.json</b></span>
<a href="http://127.0.0.1:8081" target="_blank" rel="noopener noreferrer">Open Pocket ↗</a>
<a href="http://127.0.0.1:4322/demo" target="_blank" rel="noopener noreferrer">Full Demo ↗</a></div>
<div class="al-pitch-two">
 <div><b class="al-mint">Simulated facts</b><p>Real browser hashing; tampered bytes are rejected; no wallet calls.</p></div>
 <div><b class="al-amber">Public facts</b><p>An actual Testnet Created task has been observed read-only. No real payment acceptance.</p></div>
</div>

<!-- 1:15–2:15: Show the guided evidence check and tamper rejection. -->

---
layout: default
---

<div class="al-pitch-kicker">04 / TRUST MODEL</div>
<div class="al-pitch-title">We reject <b>false success.</b></div>
<div class="al-pitch-three">
 <div class="al-pitch-tile red"><div class="al-pitch-index">PAYMENTUNKNOWN</div><h2>No recharge</h2><p>A missing ACK does not mean unpaid. Never reuse a nonce or automatically release a budget.</p><b>FAIL CLOSED</b></div>
 <div class="al-pitch-tile"><div class="al-pitch-index">IDENTITY</div><h2>Same task?</h2><p>Chain ID, Ledger, taskId, descriptionHash and payer must all match.</p><b>CROSS-LEDGER REJECTED</b></div>
 <div class="al-pitch-tile green"><div class="al-pitch-index">EVIDENCE</div><h2>Same bytes?</h2><p>Rehash canonical JSON and the original input/output independently.</p><b>BYTE MATCH ONLY</b></div>
</div>
<div class="al-pitch-bottom"><strong>Proven ≠ correct execution</strong> · USDC receipt ≠ complete payment authorization · Observed depth ≠ irreversible finality</div>

<!-- 2:15–2:35: Technical reviewers can inspect the appendix. -->

---
layout: default
---

<div class="al-pitch-kicker">05 / EVIDENCE & LIMITS</div>
<div class="al-pitch-title">What we can verify.<br><em>What we cannot claim.</em></div>
<div class="al-pitch-half">
 <div class="al-pitch-yes"><h2>✓ Implemented / checked</h2>
  <p>Expo Web Pocket with local SHA-256 verification</p>
  <p>Real Monad Testnet Created task observed read-only</p>
  <p>Cross-Ledger rejection, identity mismatch and lost ACK handling</p>
  <p>Chromium Studio/Pocket and local Anvil regressions</p>
  <p>Private read-only SDK alpha.3</p>
 </div>
 <div class="al-pitch-no"><h2>⏳ Not accepted / not claimed</h2>
  <p>Real public USDC settlement and end-to-end x402 payment</p>
  <p>The reviewed deployment registry is still empty</p>
  <p>Correctness of Agent execution or chain-state finality</p>
  <p>Native iOS installation and file-provider compatibility</p>
  <p>Production release and public npm SDK</p>
 </div>
</div>
<div class="al-pitch-bottom">Portable evidence: <b>summary.json</b> shows source, chainId, contract, task, nonce, evidenceHash and verification limits. Payment stays PaymentUnknown.</div>

<!-- 2:35–2:50: Test passing does not prove a real payment. -->

---
layout: end
class: text-center
---

<div class="al-pitch-ending">
 <div class="al-pitch-kicker">AGENTLEDGER · NEXT MILESTONE</div>
 <h1>From claims<br><em>to verifiable facts.</em></h1>
 <p>Reviewed deployment → Real Testnet USDC → Independent reconciliation → Audited Evidence</p>
 <div class="al-pitch-warning">Local verification and Testnet Created observation are demo-ready. Real USDC payment acceptance is not complete.</div>
 <div class="al-pitch-warning">Demo entry: /demo · Pocket Web · summary.json</div>
</div>

<!-- 2:50–3:00: Close and invite questions. -->
