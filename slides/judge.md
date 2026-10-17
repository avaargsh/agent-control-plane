---
theme: default
title: AgentLedger · 3-minute judge pitch
layout: cover
colorSchema: dark
class: text-center
info: |
  演示版：本地模拟与公网只读观察严格分开；不声明真实 USDC 付款验收。
---

<div class="al-pitch-cover">
  <div class="al-pitch-kicker">MONAD × x402 · AGENTLEDGER · PHASE 0</div>
  <h1>Pay → Execute → <span>Prove.</span></h1>
  <p>Agent 能为任务收费了。<br>但<strong>谁证明付款、执行与结果</strong>分别发生了？</p>
  <div class="al-pitch-tags">
    <span>Task identity</span><span>Payment observation</span><span>Evidence SHA-256</span>
  </div>
  <div class="al-pitch-warning">原型 · 公网只读任务已观察 · 真实 USDC 付款验收尚未完成</div>
</div>

<!--
0:00–0:20。开头只说问题和定位，不讲框架名单。
-->

---
layout: default
---

<div class="al-pitch-kicker">01 / THE PROBLEM</div>
<div class="al-pitch-title">Agent 的 “Success”，<br><em>不等于可信事实。</em></div>
<div class="al-pitch-three">
 <div class="al-pitch-tile"><div class="al-pitch-index">01 · PAYMENT</div><h2>Paid?</h2><p>HTTP ACK 丢失后，是已到账，还是只是没有收到回执？</p><b>Lost ACK ≠ Unpaid</b></div>
 <div class="al-pitch-tile"><div class="al-pitch-index">02 · EXECUTION</div><h2>Done?</h2><p>Recorder 声明或 Agent 自报完成，并不能证明执行所有权。</p><b>Claim ≠ Proof</b></div>
 <div class="al-pitch-tile"><div class="al-pitch-index">03 · EVIDENCE</div><h2>Verified?</h2><p>如果没有原始输入/输出和不可混淆的哈希，如何复核结果？</p><b>Hash match ≠ Correctness</b></div>
</div>
<div class="al-pitch-bottom">三个问题必须分别回答，而不是涂成一个绿色状态。</div>

<!-- 0:20–0:45。强调“未知也是有效状态”，不是一种错误。 -->

---
layout: default
---

<div class="al-pitch-kicker">02 / THE PRODUCT</div>
<div class="al-pitch-title">一个用户入口，<br><em>两层独立事实。</em></div>
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
  <div class="al-pitch-card"><b>AgentLedger Pocket</b><p>移动端任务与证据旅程；桌面端显示 Inspector 和 summary.json。</p></div>
  <div class="al-pitch-card"><b>Verification Studio + SDK</b><p>只读 <code>observe / verify / reconcile</code>，严格检查链 ID、合约、任务和哈希。</p></div>
  <div class="al-pitch-card green"><b>ACP owns execution</b><p>Lease、重试、AgentRun、执行证明继续属于已有 Control Plane。</p></div>
 </div>
</div>
<div class="al-pitch-bottom">Ledger 是结算与存证适配层，不是第二个 Agent Runtime。</div>

<!-- 0:45–1:15。这里切到 Pocket Web 实际应用，开始引导式 Demo。 -->

---
layout: default
---

<div class="al-pitch-kicker">03 / CLICKABLE DEMO</div>
<div class="al-pitch-title">一条四步旅程，<em>可当场复现。</em></div>
<div class="al-pitch-journey">
 <div class="al-pitch-stage"><i>01</i><strong>Created</strong><p>Task ID、descriptionHash 与 chainId 10143</p></div>
 <div class="al-pitch-stage"><i>02</i><strong>Paid claim</strong><p>Recorder 标记 Paid，独立支付仍未知</p></div>
 <div class="al-pitch-stage"><i>03</i><strong>Evidence</strong><p>选择 JSON + 原始文件，SHA-256 校验</p></div>
 <div class="al-pitch-stage"><i>04</i><strong>Lost ACK</strong><p>结果不明 → 不重扣、不放行</p></div>
</div>
<div class="al-pitch-demo-bar">Pocket → <b>▶ Start 3-minute guided demo</b> → Next / Back · 最后导出 <b>summary.json</b></div>
<div class="al-pitch-two">
 <div><b class="al-mint">模拟的事实</b><p>本地哈希实际计算；改变原始字节就拒绝；没有钱包调用。</p></div>
 <div><b class="al-amber">公网的事实</b><p>已有真实 Testnet 的 <em>Created</em> 只读任务观察；没有真实付款 PASS。</p></div>
</div>

<!-- 1:15–2:15。演示按钮和篡改失败，切记 Paid 只是一项声明。 -->

---
layout: default
---

<div class="al-pitch-kicker">04 / TRUST MODEL</div>
<div class="al-pitch-title">我们拒绝<b>错误的成功</b>。</div>
<div class="al-pitch-three">
 <div class="al-pitch-tile red"><div class="al-pitch-index">PAYMENTUNKNOWN</div><h2>No recharge</h2><p>丢 ACK ≠ 未扣费；不复用 nonce，也不自动释放预算。</p><b>FAIL CLOSED</b></div>
 <div class="al-pitch-tile"><div class="al-pitch-index">IDENTITY</div><h2>Same task?</h2><p>链 ID + Ledger 地址 + taskId + descriptionHash + payer 必须一致。</p><b>CROSS-LEDGER REJECTED</b></div>
 <div class="al-pitch-tile green"><div class="al-pitch-index">EVIDENCE</div><h2>Same bytes?</h2><p>规范化 JSON 与原始 input/output 分开重算 SHA-256。</p><b>BYTE MATCH ONLY</b></div>
</div>
<div class="al-pitch-bottom"><strong>合约 Proven ≠ 正确执行</strong> · USDC Receipt ≠ 完整付款意图 · 观察深度 ≠ 不可逆最终性</div>

<!-- 2:15–2:35。技术评委可转到原始全量技术稿。 -->

---
layout: default
---

<div class="al-pitch-kicker">05 / EVIDENCE & LIMITS</div>
<div class="al-pitch-title">已经能验证什么，<br><em>还不能声称什么。</em></div>
<div class="al-pitch-half">
 <div class="al-pitch-yes"><h2>✓ 已实现 / 已验证</h2>
  <p>Expo Web + Pocket 本地 Evidence 哈希</p>
  <p>真实 Monad Testnet <b>Created</b> 只读观察</p>
  <p>跨 Ledger / 错误身份拒绝、丢 ACK</p>
  <p>Studio / Pocket Chromium 与 Anvil 模拟回归</p>
  <p>私有只读 SDK alpha.3</p>
 </div>
 <div class="al-pitch-no"><h2>⏳ 未验收 / 不夸大</h2>
  <p>真实 Testnet USDC 支付与完整 x402 闭环</p>
  <p>Reviewed deployment 白名单仍为空</p>
  <p>Agent 业务执行正确性与链上最终性</p>
  <p>iOS 实机安装与文件提供者兼容</p>
  <p>生产发布、公开 npm SDK</p>
 </div>
</div>
<div class="al-pitch-bottom">单一输出：<b>summary.json</b> · 来源、chainId、contract、task、nonce、evidenceHash 与验证结论均可审查。</div>

<!-- 2:35–2:50。只挑关键边界讲，不把 CI 成功解读成支付成功。 -->

---
layout: end
class: text-center
---

<div class="al-pitch-ending">
 <div class="al-pitch-kicker">AGENTLEDGER · NEXT MILESTONE</div>
 <h1>From claims<br><em>to verifiable facts.</em></h1>
 <p>Reviewed deployment → Real Testnet USDC → Independent reconciliation → Audited Evidence</p>
 <div class="al-pitch-warning">现在可演示；真实付款闭环仍需独立验收。</div>
 <div class="al-pitch-warning">现场入口：/demo · Pocket Web · summary.json</div>
</div>

<!-- 2:50–3:00。收尾并引导提问。 -->
