---
theme: default
title: AgentLedger — Pay. Execute. Prove.
info: |
  Monad × x402 · Agent settlement observability & evidence verification
  Hackathon pitch deck. Local simulations are NOT public Testnet acceptance.
author: AgentLedger
aspectRatio: 16/9
canvasWidth: 1280
colorSchema: dark
fonts:
  sans: Noto Sans CJK SC
  mono: Noto Sans Mono
drawings:
  enabled: false
transition: fade-out
---

<div class="frame cover first">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><div class="top-label">MONAD × x402 · HACKATHON PITCH</div></div>
  <div class="globe"></div>
  <div class="content">
    <div class="section-tag" style="margin-top:25px">AGENT ECONOMY · TRUST INFRASTRUCTURE</div>
    <h1 class="title">Pay<span class="highlight">.</span><br>Execute<span class="highlight">.</span><br>Prove<span class="mint">.</span></h1>
    <div class="subtitle">让 Agent 的付款与执行证据<br>成为<span class="highlight">可独立核验的事实</span></div>
    <div class="rowline" style="margin-top:15px"><span class="pill teal">Pocket App</span><span class="pill">Read-only Verification</span><span class="pill gold">Fail Closed</span></div>
  </div>
  <div class="deck-bottom"><span>AGENTLEDGER / EXPERIMENTAL PHASE 0</span><span>Pocket guided demo · Testnet operation reported / independent acceptance pending</span></div>
</div>

<!--
开场 20 秒：我们不是在做另一个 Agent 框架。要解决的是「付过款」和「留有证据」如何被独立确认。
注意：开发者反馈已做 Monad Testnet 操作；当前仓库 reviewed 部署登记为空，未见可独立复核的完整资金回执、Nonce 与部署审查证据。请区别“跑过”“任务状态可读”“独立验收通过”。
-->

---

<div class="frame">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">02 / 13</span></div>
  <div class="section-tag">THE PROBLEM</div>
  <h1 class="head">Agent 能执行任务，<br><span class="highlight">但谁能证明事情真的发生？</span></h1>
  <p class="lead">当 Agent 开始按任务收费，调用成功的响应码无法替代可追溯的结算与执行证据。</p>
  <div class="cols three" style="margin-top:38px">
    <div class="panel"><div class="iconnum">01</div><div class="big gold">Paid?</div><h3>钱真的到账了吗</h3><p>支付响应丢失、重复扣费风险；单靠业务状态无法确认 USDC 结算。</p></div>
    <div class="panel"><div class="iconnum">02</div><div class="big highlight">Done?</div><h3>任务真的执行了吗</h3><p>回调成功、合约状态、Agent 声明都不等于独立的执行所有权证明。</p></div>
    <div class="panel"><div class="iconnum">03</div><div class="big mint">Proof?</div><h3>结果能重现核验吗</h3><p>没有原始输入输出、规范化 Evidence 和可查锚点，就无法事后审计。</p></div>
  </div>
  <div class="deck-bottom"><span>FAILURE CASE: LOST ACK ≠ UNPAID</span><span>PAYMENT FACTS · OPERATION FACTS · EVIDENCE FACTS</span></div>
</div>

<!--
分别提三个问题：支付事实、任务执行事实、证据完整性是三种不同事实。
最重要的示例：ACK 丢了，绝不能因此再收费。
-->

---

<div class="frame">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">03 / 13</span></div>
  <div class="section-tag">PRODUCT THESIS</div>
  <h1 class="head">我们要构建的，不是<span class="highlight">另一个 Agent Runtime</span></h1>
  <p class="lead">而是位于 Agent 应用之下，可观察、可核对、默认拒绝不完整证明的 <b style="color:white">Settlement + Evidence Layer</b>。</p>
  <div style="margin-top:42px;display:grid;grid-template-columns:1.08fr 0.15fr 1.1fr;gap:15px;align-items:center">
    <div class="panel lavender" style="padding:31px"><span class="pill gold">不可信的单一声明</span><div style="font-size:35px;font-weight:850;line-height:1.35;margin-top:22px">Recorder 说 <span class="gold">Paid</span><br>Agent 说 <span class="gold">Done</span><br>页面说 <span class="gold">Success</span></div><p style="margin-top:20px!important">状态只能描述主张，不能取代观察证据。</p></div>
    <div style="text-align:center;font-size:40px;color:#9d90ff">→</div>
    <div class="panel" style="padding:31px;border-color:#317c72"><span class="pill teal">可独立复核的事实</span><div style="font-size:27px;font-weight:850;line-height:1.65;margin-top:22px"><span class="mint">01</span> USDC Transfer + Nonce<br><span class="mint">02</span> 授权与任务绑定<br><span class="mint">03</span> Evidence SHA-256</div><p style="margin-top:17px!important">不一致即拒绝；未知就保持未知。</p></div>
  </div>
  <div class="deck-bottom"><span>DESIRED STATE REACHED ≠ OPERATION OWNERSHIP PROVEN</span><span>AGENTLEDGER PROVES NO MORE THAN IT OBSERVES</span></div>
</div>

<!--
强调 ACP 保留执行所有权：Ledger 不负责 lease、workflow retry 或 OutcomeContract。
付款已观察不意味着已获得不可逆最终性；哈希相等不意味着执行正确。
-->

---

<div class="frame no-orb">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">04 / 13</span></div>
  <div class="section-tag">THE USER EXPERIENCE · POCKET</div>
  <h1 class="head">一个用户 App，把复杂协议变成<span class="highlight">可信旅程</span></h1>
  <div style="display:grid;grid-template-columns:400px 1fr;gap:38px;align-items:center;margin-top:22px">
    <div class="mock" style="width:342px;height:427px">
      <div class="mock-head"><span class="mock-brand">agentledger<span class="highlight">.</span></span><span class="pill gold" style="font-size:10px;padding:5px 9px">SIMULATED</span></div>
      <div class="mock-body">
        <div style="font-size:10px;color:#a79af7;letter-spacing:.1em">YOUR TASK / READ-ONLY</div>
        <div class="mock-title">Proof, not promises</div>
        <div class="mock-card">
          <div class="between"><span style="font-weight:750;font-size:15px">Agent Task 023</span><span class="pill teal" style="font-size:10px">Proven</span></div>
          <div class="mock-row"><span>taskId</span><strong>0x2323…2323</strong></div>
          <div class="mock-row"><span>descriptionHash</span><strong>0xa1a1…a1a1</strong></div>
          <div class="mock-row"><span>Payment</span><strong style="color:#ffce84">PaymentUnknown</strong></div>
          <div style="margin-top:13px;padding:10px;border-radius:8px;background:#6252b5;color:white;text-align:center;font-size:12px">Verify Evidence SHA-256</div>
        </div>
        <div class="mock-nav"><span>Tasks</span><span>Payment</span><strong class="highlight">Evidence</strong><span>Journal</span></div>
      </div>
    </div>
    <div class="stack" style="gap:18px">
      <div class="stepbox"><div class="idx">01</div><div><b>Created → Paid claim</b><small>清楚看到 taskId / descriptionHash；Recorder 的 Paid 只是一项声明。</small></div></div>
      <div class="stepbox"><div class="idx">02</div><div><b>Evidence → Verify</b><small>移动端本地重算 SHA-256；原始 JSON 与 input / output 字节可核验。</small></div></div>
      <div class="stepbox"><div class="idx">03</div><div><b>PaymentUnknown → No recharge</b><small>丢失 ACK 后不自动重扣，不重用 Nonce，不擅自释放预留。</small></div></div>
      <div class="alert-banner">Pocket 是唯一面向现场用户的产品入口；右侧评审视图与 summary.json 展示事实来源。</div>
    </div>
  </div>
  <div class="deck-bottom"><span>EXPO WEB / MOBILE · USER FIRST</span><span>SIMULATED UI, ACTUAL LOCAL SHA-256</span></div>
</div>

<!--
演示 Pocket 的四个底部 Tab，重点展示「状态」与「付款事实」分开展示。
这一页中手机界面是示意绘制，不是实机交易截图。
-->

---

<div class="frame">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">05 / 13</span></div>
  <div class="section-tag">THE MINIMUM TRUST LOOP</div>
  <h1 class="head">四个动作，<span class="highlight">三种独立事实</span></h1>
  <p class="lead">目标闭环：先确认支付，再交给既有 Agent Runtime 执行，最后验证链上 Evidence 锚点。</p>
  <div class="flow-row" style="margin-top:44px">
    <div class="flow-item"><span class="n">01 · TASK</span><h3>Created</h3><p>链 ID、Ledger、taskId、descriptionHash、预算约束</p><div style="margin-top:22px"><span class="pill">Identity</span></div></div>
    <div class="flow-item"><span class="n">02 · SETTLEMENT</span><h3>Payment</h3><p>EIP-712 意图与预留，USDC Transfer 与 EIP-3009 nonce</p><div style="margin-top:22px"><span class="pill gold">Observe, don't assume</span></div></div>
    <div class="flow-item"><span class="n">03 · EXECUTION</span><h3>ACP / Agent</h3><p>执行调度、lease / retry、结果所有权仍由外部 Runtime 负责</p><div style="margin-top:22px"><span class="pill">External Authority</span></div></div>
    <div class="flow-item"><span class="n">04 · EVIDENCE</span><h3>Verify</h3><p>Canonical metadata SHA-256，原始输入输出 byte-level 校验</p><div style="margin-top:22px"><span class="pill teal">Digest Match</span></div></div>
  </div>
  <div class="flow-line"></div>
  <div class="gate warn" style="margin-top:33px"><b style="color:var(--gold)">当前可现场运行：</b>Pocket 引导演示 + Anvil；已有 Testnet 运行信息可只读展示，但必须单独审查交易/Nonce/部署来源。</div>
  <div class="deck-bottom"><span>IDEAL UX: PAY → EXECUTE → PROVE</span><span>PROJECT STATUS: PHASE 0 LOCAL ACCEPTANCE</span></div>
</div>

<!--
开发者称已运行过 Testnet；仍不能将运行记录等同于独立验证的真实付款验收。展示任务观察与独立结算断言的分界。
-->

---

<div class="frame no-orb">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">06 / 13</span></div>
  <div class="section-tag">SYSTEM ARCHITECTURE</div>
  <h1 class="head">只增加一层证明能力，<span class="highlight">不重建控制面</span></h1>
  <div style="display:grid;grid-template-columns:1fr 160px 1fr;gap:21px;margin-top:33px;align-items:start">
    <div class="stack">
      <div class="platform"><div class="ico">UI</div><div><b>AgentLedger Pocket</b><small>Tasks · Payment observation · Evidence · Journal</small></div></div>
      <div class="platform"><div class="ico">API</div><div><b>Read-only Verifier / SDK</b><small>observe / verify / reconcile · no signing</small></div></div>
      <div class="platform"><div class="ico">PRF</div><div><b>Evidence + Policy Gate</b><small>Hashes · canonical block · fail closed</small></div></div>
    </div>
    <div style="display:flex;flex-direction:column;align-items:center;gap:32px;margin-top:45px;color:#95a3bd;font-size:15px"><span>queries</span><div style="font-size:52px;color:var(--purple)">↔</div><span>never owns<br>execution</span><div style="font-size:52px;color:var(--teal)">↔</div></div>
    <div class="stack">
      <div class="platform"><div class="ico">AG</div><div><b>ACP / Existing Agents</b><small>Execution · ownership · recovery · outcome</small></div></div>
      <div class="platform"><div class="ico">M</div><div><b>Monad EVM + x402 / USDC</b><small>Task ledger · payment events · nonce</small></div></div>
      <div class="platform"><div class="ico">H</div><div><b>Content-addressed Evidence</b><small>On-chain digest · off-chain original bytes</small></div></div>
    </div>
  </div>
  <div style="margin-top:28px;display:grid;grid-template-columns:1fr 1fr;gap:13px">
    <div class="gate"><b>AgentLedger 负责：</b>声明与观察的差异、证据一致性、审计结果。</div>
    <div class="gate warn"><b>ACP 继续负责：</b>AgentRun、授权、lease、重试与业务完成语义。</div>
  </div>
  <div class="deck-bottom"><span>ADOPT / INTEGRATE BEFORE BUILD</span><span>NOT AN AGENT FRAMEWORK · NOT A WALLET · NOT ESCROW</span></div>
</div>

<!--
技术讲解：SDK 仍是私有只读 alpha，Pocket 经 GET-only 服务读取；不暴露可注入 transport。
链上只存摘要，不上链传输原始 Prompt、输出或个人数据。
-->

---

<div class="frame">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">07 / 13</span></div>
  <div class="section-tag">ENGINEERING WORKBENCH · STUDIO</div>
  <h1 class="head">Verification Studio：<span class="highlight">让反例也可复现</span></h1>
  <div class="cols" style="margin-top:24px;grid-template-columns:1fr 1.08fr;gap:25px">
    <div class="stack">
      <div class="stepbox"><div class="idx">✓</div><div><b>Simulated Journey</b><small>Created → Recorder Paid → Evidence → Verify</small></div></div>
      <div class="stepbox"><div class="idx">!</div><div><b>PaymentUnknown</b><small>丢 ACK / 付款结果未知 → 禁止自动重扣</small></div></div>
      <div class="stepbox"><div class="idx">×</div><div><b>Cross-Ledger / Wrong Payer</b><small>合约不同但 taskId 相同、错误付款身份都拒绝</small></div></div>
      <div class="stepbox"><div class="idx">→</div><div><b>Anvil Local Lifecycle</b><small>真实本地 EVM 部署与事件；USDC 支付为模拟</small></div></div>
    </div>
    <div class="panel" style="background:#f4f6fa;color:#1d2940;padding:24px">
      <div class="between" style="margin-bottom:17px"><span style="font-size:14px;font-weight:850;color:#6151b0">VERIFICATION STUDIO</span><span class="pill gold" style="font-size:11px">LOCAL SIMULATION</span></div>
      <div style="border-radius:12px;background:#242247;color:white;padding:22px"><div style="font-size:32px;font-weight:850;letter-spacing:-.055em">Trust the evidence.<br><span style="color:#b6a7ff">Not the status.</span></div><div style="font-size:13px;color:#c1bcd8;margin-top:9px">Three steps · Two independently checked facts</div></div>
      <div class="cols" style="gap:9px;margin-top:15px">
        <div style="border:1px solid #dbe1ed;border-radius:10px;background:white;padding:14px"><div style="font-size:12px;color:#7a7a9e">LEDGER CLAIM</div><b style="font-size:20px;color:#3d405b">Proven</b></div>
        <div style="border:1px solid #dbe1ed;border-radius:10px;background:white;padding:14px"><div style="font-size:12px;color:#7a7a9e">USDC FACT</div><b style="font-size:17px;color:#bc8951">PaymentUnknown</b></div>
      </div>
      <div style="border:1px solid #e8e8ee;border-radius:10px;background:white;padding:15px;margin-top:10px"><div style="font-size:12px;color:#838ca5">EVIDENCE DIGEST</div><b style="font-size:18px;color:#2d977d">SIMULATED HASH MATCH</b><div style="font-size:12px;color:#7f8aa0;margin-top:3px">Raw input/output bytes checked locally</div></div>
    </div>
  </div>
  <div class="deck-bottom"><span>REAL CHROMIUM NEGATIVE TESTS + FOUNDRY / ANVIL</span><span>STUDIO IS A VERIFICATION TOOL, NOT ANOTHER USER APP</span></div>
</div>

<!--
技术评审可直接打开 Studio 演示，现场观众则始终优先展示 Pocket。
Chrome 交互、Foundry 和 Anvil 测试是本地工程证据。
-->

---

<div class="frame">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">08 / 13</span></div>
  <div class="section-tag">OFFLINE FIRST · PRE-FLIGHT</div>
  <h1 class="head"><span class="highlight">readiness.json</span>：进场之前先证明能演示</h1>
  <div class="cols" style="grid-template-columns:.96fr 1.04fr;gap:30px;margin-top:29px">
    <div class="stack">
      <div class="stepbox"><div class="idx">01</div><div><b>离线检查文件与角色边界</b><small>Pocket / Studio / SDK / reviewed registry · 0 网络调用</small></div></div>
      <div class="stepbox"><div class="idx">02</div><div><b>生成机器可读的 readiness.json</b><small>记录 safeToDemo 与 public Testnet acceptance 状态</small></div></div>
      <div class="stepbox"><div class="idx">03</div><div><b>只读联网预检（可选）</b><small>eth_chainId + GET /supported；不签名、不广播、不结算</small></div></div>
      <div class="gate warn"><b>重要：</b>预检 PASS 只意味着演示环境就绪，不意味着 USDC 支付发生。</div>
    </div>
    <div>
      <pre class="code-box"><span style="color:#8dcfff">$ npm run onsite:check</span>
{
  "mode": <span style="color:#8df2cd">"LOCAL_ONLY_NO_NETWORK"</span>,
  "safeToDemo": <span style="color:#8df2cd">true</span>,
  "safeToClaimPublicTestnetAcceptance": <span style="color:#ffcc88">false</span>,
  "payment": {
    "state": <span style="color:#ffcc88">"PaymentUnknown"</span>,
    "chargeAgain": <span style="color:#8df2cd">false</span>,
    "actualUSDCSpend": <span style="color:#8df2cd">false</span>
  }
}</pre>
      <div class="tagline" style="margin-top:14px">No wallet · No private key · No signature · No transaction</div>
    </div>
  </div>
  <div class="deck-bottom"><span>DEMO READINESS ≠ SETTLEMENT FINALITY</span><span>REPRODUCIBLE · AUDITABLE · OFFLINE SAFE</span></div>
</div>

<!--
展示 CLI 运行方式：apps/pocket 下 npm run onsite:check。示例 JSON 是字段节选。
网络预检仅检查接口宣称支持，不说明已 /settle。
-->

---

<div class="frame">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">09 / 13</span></div>
  <div class="section-tag">SECURITY SEMANTICS</div>
  <h1 class="head">最重要的能力：<span class="highlight">不确定时，不做错事</span></h1>
  <div class="cols three" style="margin-top:46px">
    <div class="panel"><span class="pill gold">Lost ACK</span><h3 style="margin-top:23px!important">PaymentUnknown</h3><p>支付响应丢失不代表未扣款。固定 Nonce / reservation，禁止擅自重发。</p><div class="rule"></div><div class="foot gold">NO RECHARGE</div></div>
    <div class="panel"><span class="pill red">Cross-Ledger</span><h3 style="margin-top:23px!important">Identity Mismatch</h3><p>同一个 taskId 若来自另一合约，不可混合 USDC 回执与 Evidence。</p><div class="rule"></div><div class="foot red">REJECT PROOF</div></div>
    <div class="panel"><span class="pill gold">Unreviewed Deploy</span><h3 style="margin-top:23px!important">Public Acceptance Blocked</h3><p>没有审核过的 bytecode、Recorder、创建交易与注册表，拒绝公网通过报告。</p><div class="rule"></div><div class="foot gold">FAIL CLOSED</div></div>
  </div>
  <div class="gate" style="margin-top:31px"><b style="color:var(--teal)">独立复核：</b>chainId / contract / taskId / descriptionHash / payer / amount / Transfer / nonce / Evidence digest</div>
  <div class="deck-bottom"><span>UNKNOWN ≠ FAILED · HASH MATCH ≠ EXECUTION CORRECTNESS</span><span>NEGATIVE CASES ARE FIRST-CLASS DEMO FEATURES</span></div>
</div>

<!--
安全 slide 应突出：未知不重扣；错身份不入账；未经 review 的部署不出公链通过。
-->

---

<div class="frame">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">10 / 13</span></div>
  <div class="section-tag">WHY MONAD × x402</div>
  <h1 class="head">面向<span class="highlight">高频 Agent 微任务</span>的结算观察层</h1>
  <p class="lead">并行 EVM 为高吞吐链上交互提供方向，x402 为 HTTP 付费意图提供统一表达；AgentLedger 负责把观察结果转化成可审计证据。</p>
  <div class="cols three" style="margin-top:43px">
    <div class="panel lavender"><div class="iconnum">M</div><h3>Monad · EVM</h3><p>适配链上 Task Ledger、Recorder 事件及 Evidence 哈希锚点。</p><div class="foot">eip155:10143 · Testnet target</div></div>
    <div class="panel lavender"><div class="iconnum">402</div><h3>x402 · HTTP Payment</h3><p>签名意图、预留与回执观察分离；不依赖一次同步 ACK 判定到账。</p><div class="foot">USDC Transfer / EIP-3009</div></div>
    <div class="panel lavender"><div class="iconnum">✓</div><h3>AgentLedger · Proof</h3><p>只读 observe / verify / reconcile；适配任意 Agent Framework。</p><div class="foot">Evidence SHA-256 / Audit log</div></div>
  </div>
  <div class="alert-banner" style="margin-top:35px">我们不展示未经证实的 TPS、最终性或已付款指标；展示的是协议边界、可复核事件与安全失败路径。</div>
  <div class="deck-bottom"><span>NOT A PAYMENT NETWORK · AN OBSERVATION & EVIDENCE LAYER</span><span>MONAD TESTNET INTEGRATION PENDING REVIEWED DEPLOYMENT</span></div>
</div>

<!--
Monad 的高性能特性是目标环境的技术背景，不对项目实际性能做无证据承诺。
-->

---

<div class="frame">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">11 / 13</span></div>
  <div class="section-tag">LIVE DEMO · 2 MINUTES</div>
  <h1 class="head">现场只演<span class="highlight">一条产品线</span>，然后亮出反例</h1>
  <div class="timeline">
    <div class="time-step"><span class="pill">0:00—0:25</span><b>Start guided demo → Created</b><p>一键开始；查看 taskId、descriptionHash、chain ID。</p></div>
    <div class="time-step"><span class="pill gold">0:25—0:50</span><b>Next → Recorder Paid</b><p>跟随固定下一步按钮，区分 Recorder 声明和 USDC 独立核验。</p></div>
    <div class="time-step"><span class="pill teal">0:50—1:20</span><b>Next → Evidence → Verify</b><p>本机 SHA-256 真校验；进入 summary.json 展示来源。</p></div>
    <div class="time-step"><span class="pill red">1:20—2:00</span><b>Next → Audit → Lost ACK</b><p>零重扣；必要时切 Studio 展示错身份拒绝。</p></div>
  </div>
  <div class="cols" style="margin-top:24px;grid-template-columns:.92fr 1.08fr">
    <div class="panel"><div class="meta">MAIN ENTRY</div><div style="font-size:24px;font-weight:800">AgentLedger Pocket</div><p style="margin-top:8px!important">现场观众看到完整的任务与证据旅程。</p><div style="margin-top:13px" class="pill teal">USER EXPERIENCE</div></div>
    <div class="panel"><div class="meta">ENGINEERING EVIDENCE</div><div style="font-size:24px;font-weight:800">Studio + readiness.json</div><p style="margin-top:8px!important">工程评委可看本地 Anvil、负向测试、summary.json。</p><div style="margin-top:13px" class="pill">INDEPENDENT REVIEW</div></div>
  </div>
  <div class="deck-bottom"><span>DEMO ARTIFACTS: readiness.json · summary.json · SCREENSHOTS</span><span>ALL LOCAL / SIMULATED SCOPES LABELED</span></div>
</div>

<!--
约两分钟。不要先展示控制台。Pocket 顶部固定的 Next 引导了 Created、Recorder 声明、Evidence 校验、summary.json 和 Lost ACK；最后才打开 Studio 反例。
如实标注：支付是模拟/Recorder claim；Evidence 的字节校验是真哈希。
-->

---

<div class="frame">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><span class="number">12 / 13</span></div>
  <div class="section-tag">CURRENT STATE · EVIDENCE DISCIPLINE</div>
  <h1 class="head">我们已经验证了什么，<span class="highlight">还没有验证什么</span></h1>
  <div class="cols" style="margin-top:26px;grid-template-columns:1fr 1fr;gap:25px">
    <div class="panel" style="border-color:#287866"><span class="pill teal">LOCAL VERIFIED</span>
      <div class="stack" style="margin-top:23px;gap:18px">
        <div class="rowline"><span class="mint" style="font-size:24px">✓</span><span>Expo Pocket 可交互流程与 SHA-256 原始字节校验</span></div>
        <div class="rowline"><span class="mint" style="font-size:24px">✓</span><span>Studio Chromium 负向测试、错身份拒绝</span></div>
        <div class="rowline"><span class="mint" style="font-size:24px">✓</span><span>Foundry / Anvil 本地合约生命周期</span></div>
        <div class="rowline"><span class="mint" style="font-size:24px">✓</span><span>offline-first readiness.json 与 summary.json</span></div>
      </div>
    </div>
    <div class="panel" style="border-color:#8c6147"><span class="pill gold">PUBLIC ACCEPTANCE BLOCKED</span>
      <div class="stack" style="margin-top:23px;gap:18px">
        <div class="rowline"><span class="gold" style="font-size:24px">○</span><span>审核过的 Monad Testnet 合约部署</span></div>
        <div class="rowline"><span class="gold" style="font-size:24px">○</span><span>已运行 Testnet 的交易哈希 / USDC 事件 / Nonce 证据归档审查</span></div>
        <div class="rowline"><span class="gold" style="font-size:24px">○</span><span>完整 EIP-712 / precharge / Recorder 真实联调</span></div>
        <div class="rowline"><span class="gold" style="font-size:24px">○</span><span>不可逆最终性、独立 Agent 执行证明</span></div>
      </div>
    </div>
  </div>
  <div class="gate warn" style="margin-top:26px"><b>当前准确表述：</b>本地演示与负向测试通过；开发者报告 Testnet 已运行，但尚需将部署、付款事件和 Nonce 形成可独立复查的验收材料。</div>
  <div class="deck-bottom"><span>NO REVIEWED DEPLOYMENT → NO PUBLIC SUCCESS CLAIM</span><span>HONEST PROVENANCE IS PART OF THE PRODUCT</span></div>
</div>

<!--
评委最在意「哪些是真的」。主动披露未完成的公网支付，反而能强化对项目安全边界的信任。
-->

---

<div class="frame cover">
  <div class="topline"><div class="brand">agentledger<span>.</span></div><div class="top-label">NEXT MILESTONE</div></div>
  <div class="globe" style="opacity:.7"></div>
  <div class="content">
    <div class="section-tag" style="margin-top:40px">OPEN INFRASTRUCTURE FOR THE AGENT ECONOMY</div>
    <h1 class="title" style="font-size:75px!important;margin:32px 0 24px!important">把信任从<span class="highlight">承诺</span><br>变成<span class="mint">证据</span><span class="highlight">.</span></h1>
    <div class="subtitle" style="font-size:28px">下一步：Review Testnet run evidence → Deployment provenance<br>→ Independent Reconcile → Audited Evidence</div>
    <div class="rowline" style="margin-top:32px"><span class="pill teal">Open Source</span><span class="pill">Pocket · Studio · Read-only SDK</span></div>
    <div class="code-preview" style="margin-top:27px;width:680px;font-size:17px">github.com/avaargsh/agent-control-plane<br>experiments/agentledger</div>
  </div>
  <div class="deck-bottom"><span>AGENTLEDGER / PAY → EXECUTE → PROVE</span><span>THANK YOU · MONAD × x402</span></div>
</div>

<!--
收尾：项目价值是可审计基础设施，不是竞争 Agent 框架。
如需帮忙，可邀请官方确认 x402 facilitator 的 nonce / lost ACK 推荐对账语义及 reviewed deployment。
-->
