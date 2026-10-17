---
theme: default
title: AgentLedger · 三分钟评委路演（中文）
layout: cover
colorSchema: dark
class: text-center
info: |
  演示版：本地模拟与公网只读观察严格分开；不声明真实 USDC 付款验收。
---

<div class="al-pitch-cover">
  <div class="al-pitch-language"><span>语言</span><a href="../dist-judge-en/" aria-label="Switch to English">English ↗</a></div>
  <div class="al-pitch-kicker">AGENTLEDGER · MONAD × x402 · 评委演示</div>
  <h1>付款 → 执行 → <span>核验。</span></h1>
  <p>Agent 能为任务收费了。<br>但<strong>谁证明付款、执行与结果</strong>分别发生了？</p>
  <div class="al-pitch-tags">
    <span>01 · 任务</span><span>02 · PaymentUnknown</span><span>03 · 证据</span>
  </div>
  <div class="al-pitch-flow">3 分钟短讲 <b>→</b> Pocket 四步导览 <b>→</b> 完整演示六步离线核验 + 可选测试网只读</div>
  <div class="al-pitch-warning">原型 · 公网只读任务已观察 · 真实 USDC 付款验收尚未完成</div>
</div>

<!--
0:00–0:20。开头只说问题和定位，不讲框架名单。
-->

---
layout: default
---

<div class="al-pitch-kicker">01 / 核心问题</div>
<div class="al-pitch-title">Agent 的 “Success”，<br><em>不等于可信事实。</em></div>
<div class="al-pitch-three">
 <div class="al-pitch-tile"><div class="al-pitch-index">01 · 付款</div><h2>是否付款？</h2><p>HTTP ACK 丢失后，是已到账，还是只是没有收到回执？</p><b>响应丢失 ≠ 未扣款</b></div>
 <div class="al-pitch-tile"><div class="al-pitch-index">02 · 执行</div><h2>是否执行？</h2><p>Recorder 声明或 Agent 自报完成，并不能证明执行所有权。</p><b>声明 ≠ 证明</b></div>
 <div class="al-pitch-tile"><div class="al-pitch-index">03 · 证据</div><h2>如何核验？</h2><p>如果没有原始输入/输出和不可混淆的哈希，如何复核结果？</p><b>哈希一致 ≠ 结果正确</b></div>
</div>
<div class="al-pitch-bottom">三个问题必须分别回答，而不是涂成一个绿色状态。</div>

<!-- 0:20–0:45。强调“未知也是有效状态”，不是一种错误。 -->

---
layout: default
---

<div class="al-pitch-kicker">02 / 产品入口</div>
<div class="al-pitch-title">一个用户入口，<br><em>两层独立事实。</em></div>
<div class="al-pitch-product">
 <div class="al-pitch-phone">
  <div class="al-pitch-ph-top"><strong>agentledger.</strong><span>只读</span></div>
  <div class="al-pitch-ph-hero">任务结果，<br><strong>可独立核验。</strong></div>
  <div class="al-pitch-ph-row"><span>任务 ID / 描述哈希</span><b>Created</b></div>
  <div class="al-pitch-ph-row"><span>记录者声明</span><b class="al-amber">付款声明</b></div>
  <div class="al-pitch-ph-row"><span>USDC 结算</span><b class="al-amber">PaymentUnknown</b></div>
  <div class="al-pitch-ph-row"><span>证据 SHA-256</span><b class="al-mint">核验字节</b></div>
  <div class="al-pitch-ph-tabs">任务　·　支付　·　证据　·　记录</div>
 </div>
 <div class="al-pitch-right">
  <div class="al-pitch-card"><b>AgentLedger Pocket</b><p>移动端任务与证据旅程；桌面端显示 核验详情及 summary.json。</p></div>
  <div class="al-pitch-card"><b>核验工作室 + SDK</b><p>只读 <code>observe / verify / reconcile</code>，严格检查链 ID、合约、任务和哈希。</p></div>
  <div class="al-pitch-card green"><b>无隐含扣款权限</b><p>展示端只读、哈希在本地核验；不发起扣款或 Agent 任务执行。</p></div>
 </div>
</div>
<div class="al-pitch-bottom">账本负责结算观察与证据锚定，不负责智能体业务执行。</div>

<!-- 0:45–1:15。这里切到 Pocket Web 实际应用，开始引导式 Demo。 -->

---
layout: default
---

<div class="al-pitch-kicker">03 / 可交互演示</div>
<div class="al-pitch-title">一条四步旅程，<em>可当场复现。</em></div>
<div class="al-pitch-journey">
 <div class="al-pitch-stage"><i>01</i><strong>已创建</strong><p>Task ID、descriptionHash 与 chainId 10143</p></div>
 <div class="al-pitch-stage"><i>02</i><strong>付款声明</strong><p>Recorder 标记 Paid，独立支付仍未知</p></div>
 <div class="al-pitch-stage"><i>03</i><strong>证据</strong><p>选择 JSON + 原始文件，SHA-256 校验</p></div>
 <div class="al-pitch-stage"><i>04</i><strong>响应丢失</strong><p>结果不明 → 不重扣、不放行</p></div>
</div>
<div class="al-pitch-demo-bar"><span>Pocket：开始导览 → 下一步 / 上一步 → 响应丢失 → <b>summary.json</b></span>
<a href="http://127.0.0.1:8081" target="_blank" rel="noopener noreferrer">打开 Pocket ↗</a>
<a href="http://127.0.0.1:4322/demo" target="_blank" rel="noopener noreferrer">完整演示 ↗</a></div>
<div class="al-pitch-two">
 <div><b class="al-mint">模拟的事实</b><p>本地哈希实际计算；改变原始字节就拒绝；没有钱包调用。</p></div>
 <div><b class="al-amber">公网的事实</b><p>已有真实测试网的 <em>Created</em> 只读任务观察；没有真实付款验收。</p></div>
</div>

<!-- 1:15–2:15。演示按钮和篡改失败，切记 Paid 只是一项声明。 -->

---
layout: default
---

<div class="al-pitch-kicker">04 / 信任模型</div>
<div class="al-pitch-title">拒绝<b>虚假的成功</b>。</div>
<div class="al-pitch-three">
 <div class="al-pitch-tile red"><div class="al-pitch-index">支付未知</div><h2>禁止重扣</h2><p>丢 ACK ≠ 未扣费；不复用 nonce，也不自动释放预算。</p><b>失败时拒绝放行</b></div>
 <div class="al-pitch-tile"><div class="al-pitch-index">身份</div><h2>任务身份一致？</h2><p>链 ID + Ledger 地址 + taskId + descriptionHash + payer 必须一致。</p><b>拒绝跨账本混淆</b></div>
 <div class="al-pitch-tile green"><div class="al-pitch-index">证据</div><h2>字节一致？</h2><p>规范化 JSON 与原始 input/output 分开重算 SHA-256。</p><b>仅证明字节一致</b></div>
</div>
<div class="al-pitch-bottom"><strong>合约 Proven ≠ 正确执行</strong> · USDC Receipt ≠ 完整付款意图 · 观察深度 ≠ 不可逆最终性</div>

<!-- 2:15–2:35。技术评委可转到原始全量技术稿。 -->

---
layout: default
---

<div class="al-pitch-kicker">05 / 证据与边界</div>
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
<div class="al-pitch-bottom">可带走的证据：<b>summary.json</b> · 来源、chainId、contract、task、nonce、evidenceHash 与验证边界。付款仍为 PaymentUnknown。</div>

<!-- 2:35–2:50。只挑关键边界讲，不把 CI 成功解读成支付成功。 -->

---
layout: end
class: text-center
---

<div class="al-pitch-ending">
 <div class="al-pitch-kicker">AGENTLEDGER · 下一阶段</div>
 <h1>从状态声明，<br><em>到可核验的事实。</em></h1>
 <p>部署审核 → 真实测试网 USDC 支付 → 独立对账 → 证据审计</p>
 <div class="al-pitch-warning">可以现场演示本地校验 + Testnet Created 只读；真实 USDC 付款闭环仍未验收。</div>
 <div class="al-pitch-warning">演示入口：/demo · Pocket 网页端 · summary.json</div>
</div>

<!-- 2:50–3:00。收尾并引导提问。 -->
