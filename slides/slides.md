---
theme: default
colorSchema: dark
layout: cover
class: text-center
highlighter: shiki
lineNumbers: false
title: AgentLedger — Pay → Execute → Prove
info: |
  AgentLedger 黑客松评审稿：结算观察 + 证据层
---

<div class="al-cover">

<div class="al-eyebrow">AGENTLEDGER · EXPERIMENTAL PHASE 0</div>

# Pay → Execute → Prove

<div class="al-sub">让 Agent 的付款与执行证据<br>成为<em>可独立核验</em>的事实</div>

<div class="al-pill">Monad Testnet × x402</div>
<div class="al-pill mint">Read-only · Fail-closed · No wallet</div>
<div class="al-pill amber">本地可复现演示已就绪 · 公网 Testnet 付款验收尚未完成</div>

</div>

<!--
开场：一句话定位。AgentLedger 不是又一个 Agent 框架，
而是位于它们下方的可审计结算与证据基板。
-->

---
layout: default
---

<div class="al-title">当 Agent 开始按任务收费，<br>谁证明事情真的发生了？</div>

<div class="al-grid3">

<div class="al-q">
<div class="al-num">01 · Paid?</div>
<div class="al-qhead">钱真的到账了吗</div>
<div>支付响应丢失、重复扣费风险；单靠业务状态无法确认 <b>USDC 结算</b>。</div>
<div class="al-tiny">LOST ACK ≠ UNPAID</div>
</div>

<div class="al-q">
<div class="al-num">02 · Done?</div>
<div class="al-qhead">任务真的执行了吗</div>
<div>回调成功、合约状态、Agent 声明，都<b>不等于</b>独立的执行所有权证明。</div>
<div class="al-tiny">CLAIM ≠ OPERATION OWNERSHIP</div>
</div>

<div class="al-q">
<div class="al-num">03 · Proof?</div>
<div class="al-qhead">结果能重现核验吗</div>
<div>没有原始输入/输出、规范化 Evidence 与可查锚点，就无法事后审计。</div>
<div class="al-tiny">NO ANCHOR · NO AUDIT</div>
</div>

</div>

<Footer />

---
layout: default
---

<div class="al-title">不是另一个 Agent Runtime，<br>而是它下方的一层——<b>Settlement + Evidence</b></div>

<div class="al-grid2">

<div class="al-q red">
<div class="al-num">不可信的单一声明</div>
<ul><li>Recorder 说 Paid</li><li>Agent 说 Done</li><li>页面说 Success</li></ul>
<div class="al-tiny">状态只能描述主张，不能取代观察证据</div>
</div>

<div class="al-q mint">
<div class="al-num">可独立复核的事实</div>
<ul><li>USDC <code>Transfer</code> + EIP-3009 nonce</li><li>授权签名与任务绑定</li><li>Evidence SHA-256 锚点</li></ul>
<div class="al-tiny">不一致即拒绝；未知就保持未知</div>
</div>

</div>

<div class="al-bar">AGENTLEDGER PROVES NO MORE THAN IT OBSERVES</div>

---
layout: default
---

<div class="al-title">四个动作 · 三个独立事实来源</div>

<div class="al-flow">

<div class="al-step"><div class="al-none">01 · TASK</div><div class="al-huge">Identity</div><div>链 ID / Ledger / <code>taskId</code> / <code>descriptionHash</code> / 预算上限</div></div>
<div class="al-arrow">→</div>
<div class="al-step"><div class="al-none">02 · SETTLEMENT</div><div class="al-huge">Payment</div><div>签名意图与预留 → USDC <code>Transfer</code> / EIP-3009 nonce 独立观察</div></div>
<div class="al-arrow">→</div>
<div class="al-step"><div class="al-none">03 · EXECUTION</div><div class="al-huge">ACP / Agent</div><div>调度、lease、重试与结果所有权仍归<b>既有 Runtime</b></div></div>
<div class="al-arrow">→</div>
<div class="al-step"><div class="al-none">04 · EVIDENCE</div><div class="al-huge">Verify</div><div>Canonical 元数据 SHA-256 + 原始输入输出逐字节核验</div></div>

</div>

<div class="al-bar">「付了 → 执行了 → 证据可验证」的最小可信任闭环</div>

---
layout: default
---

<div class="al-title">只加一层证明能力，不重建控制面</div>

<div class="al-grid3">

<div class="al-q">
<div class="al-num">UI · Pocket</div>
<div>用户旅程 <b>Task → 支付观察 → 证据核验</b>。四个 Tab：Tasks / Payment / Evidence / Journal，桌面手机框 + Inspector。</div>
</div>
<div class="al-q">
<div class="al-num">Verifier + SDK</div>
<div>只读三操作 <code>observe / verify / reconcile</code>；拒绝注入 transport；策略门在观察之上 fail-closed。</div>
</div>
<div class="al-q">
<div class="al-num">AgentLedger 合约</div>
<div>Monad EVM 上的 Task ledger、支付事件与 nonce 占用；<b>永不拥有</b>执行权。</div>
</div>
<div class="al-q al-widerow">
<div class="al-num">Evidence</div>
<div>内容寻址：链上存 digest，链下存原始字节；SHA-256 逐字节可复算。</div>
</div>
<div class="al-q al-widerow mint">
<div class="al-num">既有的 Agent 框架</div>
<div>AgentRun、授权、重试、业务完成语义原样保留——Ledger 只是结算/佐证适配层，<b>不是第二控制面</b>。</div>
</div>

</div>

---
layout: default
---
<div class="al-title">只读 SDK 用法：@agentledger/verify v0.2.0-alpha.3</div>

<div class="tabs-2col">

```js
import {createAgentLedgerVerifier} from "@agentledger/verify";

const sdk = createAgentLedgerVerifier({
  rpcUrl: "https://testnet-rpc.monad.xyz"
});

// 01 · observe：只读查询任务，不签名
const observed = await sdk.observe(contract, taskId);
// observed.status.state === "PaymentUnknown"
// observed.chargeAgain === false

// 02 · verify：本地字节 vs 链上 SHA-256 锚点
const matched = await sdk.verify(contract, taskId, evidence, {
  inputBytes, outputBytes
});
// matched.match === true 仅当规范化字节一致

// 03 · reconcile：只读观察 USDC Transfer + EIP-3009 nonce
const receipt = await sdk.reconcile({
  network:"eip155:10143", taskId, transaction, payer, payTo,
  amountAtomic:"1000", paymentNonce:"0x..."
}, {minConfirmations:2});
// receipt.status === "PaymentUnknown" 尽管 Transfer 有效
```

<div class="al-q mt6">

- 能力集：`observe / verify / reconcile` = true；`charge / sign / mutateLedger / executeAgent` = false
- 构造即拒绝：注入 `client`/`transport`/`fetch`/`request` → `SDK_TRANSPORT_FORBIDDEN`
- 只读 RPC：仅固定 HTTPS / 本机回环；凭证、路径、查询片段拒绝
- 错误契约：`AgentLedgerSDKError`，稳定 `code/operation/retryable`，`paymentStatus="PaymentUnknown"`，`chargeAgain=false`
- 支付状态恒为 `PaymentUnknown`，永不升级；`verify` 匹配 ≠ 执行正确
- `private:true`，不发布 npm；Node 22 / 24；14 项契约测试全绿

</div>

</div>

---
layout: default
---

<div class="al-title">本轮整合：单一事实来源</div>

<div class="al-grid2">

<div class="al-q">
<div class="al-num">并入</div>
<ul>
<li><b>apps/pocket</b>（Expo RN + Web）整套用户侧应用</li>
<li><b>sdk → 0.2.0-alpha.3</b>：v1 稳定错误码、类型化 <code>index.d.mts</code>、packcheck、Node 22/24 契约测试</li>
<li>Verifier 对 Pocket 的<b>显式回环 CORS</b> 白名单 + 专项测试</li>
</ul>
</div>

<div class="al-q mint">
<div class="al-num">去重</div>
<ul>
<li>docs 旧路径统一为<b>仓库相对路径</b>（<code>experiments/agentledger/…</code> → 本仓）</li>
<li>移除队友快照副本与 macOS 压缩残留；Pitch PDF 归档至 <code>docs/</code></li>
<li>git status 中进行中的 PR #157 工作全部纳入同一提交</li>
</ul>
</div>

</div>

<div class="al-bar">全量测试：Node <b>124</b> + SDK <b>14</b> + Pocket <b>15</b> + Python <b>12</b> 全绿</div>

<!--
备注：本轮把队友的 Pocket/SDK 工作与主仓库的
Verifier/工作台/Chromium 验收合并成单一树。
-->

---
layout: default
---

<div class="al-title">Pocket：用户视角的完整旅程</div>

<div class="al-grid3">

<div class="al-q">
<div class="al-num">01 · 真实 SHA-256</div>
<div>Expo Web 与 React Native <b>共用一套</b> <code>evidence.ts</code> 规范化与哈希代码；模拟数据使用<b>预先固定的参考哈希</b>，能发现任务身份、摘要与原始输入/输出不一致。</div>
</div>

<div class="al-q">
<div class="al-num">02 · 只读 Testnet 查询</div>
<div>输入 Ledger 合约地址 + Task ID，经现有 verifier <code>GET /api/task</code> 读取；校验 chainId <b>10143</b>、合约地址、Task ID 与返回字段。</div>
</div>

<div class="al-q">
<div class="al-num">03 · 证据检查页</div>
<div>粘贴 Evidence JSON 重新计算摘要并与任务 <code>evidenceHash</code> 比较；可选填 UTF-8 输入/输出文本。改一个字节立刻拒绝。</div>
</div>

<div class="al-q">
<div class="al-num">04 · 请求安全</div>
<div>仅 HTTPS 或本机回环；不允许任意 RPC transport；<b>切换任务即丢弃过期查询</b>（epoch 失效）；10 s 超时、64 KiB 响应上限、拒绝重定向与非 JSON。</div>
</div>

<div class="al-q">
<div class="al-num">05 · summary.json 导出</div>
<div>桌面 Inspector / 移动端 Journal 导出；序列化器<b>拒绝</b>声称已付 USDC、允许重扣或把本地模拟冒充公网验收的报告。</div>
</div>

<div class="al-q">
<div class="al-num">06 · 桌面评审面板</div>
<div>模拟证据标注 <code>LOCAL_SIMULATION</code>；真实任务标注 <code>TESTNET_TASK_READ_ONLY</code>；保留 <code>PaymentUnknown</code> 与<code>BLOCKED_NO_REVIEWED_DEPLOYMENT</code> 状态。</div>
</div>

</div>

---
layout: default
---

<div class="al-title">Verification Studio：让反例也能复现</div>

<div class="al-grid2">

<div class="al-q">
<div class="al-num">模拟旅程</div>
<div>Created → Recorder Paid（<b>仅声明</b>）→ Evidence → Verify；旁路 <b>Lost ACK / PaymentUnknown</b>：不重扣、不重用 nonce、不释放预留。</div>
</div>

<div class="al-q">
<div class="al-num">真实 Testnet 只读</div>
<div>钉住 <code>creation tx</code> + 合约 + taskId，每次新鲜读取 Monad Testnet；RPC 错误与身份不匹配都是<b>可见失败</b>（fail-closed），视图不做缓存伪装。</div>
</div>

<div class="al-q red">
<div class="al-num">负向测试</div>
<div>跨合约同 taskId、错误付款人身份<b>一律拒绝</b>；真实 Chromium 点击路径 + 桌面/手机(390px)截图 + 文件下载契约。</div>
</div>

<div class="al-q mint">
<div class="al-num">Anvil 本地生命周期</div>
<div>真实本地 EVM 部署 → 创建 → 事件 → 证据 → UI 回读；<b>USDC 支付为模拟</b>，从不冒充真实结算。</div>
</div>

</div>

<div class="al-bar">STUDIO IS A VERIFICATION TOOL, NOT ANOTHER USER APP</div>

---
layout: default
---

<div class="al-title">证据规范：规范化 + SHA-256</div>

<div class="tabs-2col">

```json
{
  "taskId": "0x…(bytes32)",
  "executor": "…1–128",
  "modelOrTool": "…1–128",
  "completedAt": "ISO-8601 ms UTC",
  "inputSha256":  "0x…(bytes32)",
  "outputSha256": "0x…(bytes32)",
  "schemaVersion": "agentledger.evidence.v1"
}
```

<div class="al-q mt6">

- 固定键集 + 键的词典序、无多余空白/键
- 大写严格 `BYTES32`、ISO-8601 毫秒 UTC
- SHA-256 覆盖 <b>canonical 字节</b>；原始 input/output 单独按字节再哈希
- 浏览器 WebCrypto 与 CLI 保持<b>字节一致</b>（同一份代码）
- 切换任务、替换文件、改动文本 → 立即作废旧匹配

</div>

</div>

<div class="al-bar red">篡改摘要、任务、原始字节或字段集 → 一律拒绝 · 缺失上下文 → 保持 PaymentUnknown</div>

---
layout: default
---

<div class="al-title">Phase 0：只读的外部观察契约</div>

<div class="al-state-grid">

<div class="al-step sm"><div class="al-none">Created</div><div>任务已登记；<b>不授权任何支出</b></div></div>
<div class="al-step sm"><div class="al-none">PaymentUnknown</div><div>结论未知 ≠ 未扣款；<b>禁止</b>自动重扣</div></div>
<div class="al-step sm"><div class="al-none">PaymentObserved</div><div>独立核对 USDC Transfer + nonce（链仍 Created）</div></div>
<div class="al-step sm"><div class="al-none">PaymentRecorded</div><div>+ Recorder 链上声明一致匹配</div></div>
<div class="al-step sm"><div class="al-none">EvidenceAnchored</div><div>+ 链上 Proven 且本地 bytes 哈希匹配</div></div>
<div class="al-step sm red"><div class="al-none">Conflict</div><div>独立观察不一致；<b>永不降级、永不重扣</b></div></div>

</div>

<div class="al-bar">Created is a task record · Paid is a recorder claim · hash match = byte consistency.
<strong>None</strong> establishes independently verified USDC settlement or correct Agent execution.</div>

---
layout: default
---

<div class="al-title">最重要的能力：不确定时，不做错事</div>

<div class="al-grid3">

<div class="al-q red">
<div class="al-num">Lost ACK</div>
<div class="al-huge">NO RECHARGE</div>
<div>支付响应丢失 ≠ 未扣款。固定 nonce / reservation，禁止擅自重发或释放。</div>
</div>

<div class="al-q red">
<div class="al-num">Cross-Ledger</div>
<div class="al-huge">REJECT PROOF</div>
<div>同一 taskId 若来自另一合约，不可混用 USDC 回执与 Evidence。</div>
</div>

<div class="al-q red">
<div class="al-num">Unreviewed Deploy</div>
<div class="al-huge">FAIL CLOSED</div>
<div>无审核过的 bytecode / Recorder / 创建交易 / 注册表 → 拒绝公网通过报告。</div>
</div>

</div>

<div class="al-obs">独立复核：chainId · contract · taskId · descriptionHash · payer · amount · Transfer · nonce · Evidence digest</div>
<div class="al-bar">UNKNOWN ≠ FAILED · HASH MATCH ≠ EXECUTION CORRECTNESS</div>

---
layout: default
---

<div class="al-title">进场之前，先证明能演示</div>

<div class="tabs-2col">

```bash
$ npm run onsite:check
```

```json
{
  "mode": "LOCAL_ONLY_NO_NETWORK",
  "safeToDemo": true,
  "walletRequired": false,
  "privateKeyRequired": false,
  "networkCalls": false,
  "safeToClaimPublicTestnetAcceptance": false,
  "payment": {
    "state": "PaymentUnknown",
    "chargeAgain": false,
    "actualUSDCSpend": false
  }
}
```

<div class="al-q mt6">

- 离线检查关键文件与角色边界；`--network` 时仅限回环 `GET /api/healthz`（chainId 必须是 10143）
- 无钱包、无私钥、无签名、无交易
- 可选 facilitator 预检：只读 `/supported`，HTTPS 强制，缺失方案即 fail closed

</div>

</div>

<div class="al-bar amber">预检 PASS 只意味着演示环境就绪，绝不意味着 USDC 支付发生</div>

---
layout: default
---

<div class="al-title">为什么是 Monad × x402</div>

<div class="al-flow">

<div class="al-step">
<div class="al-none">M · MONAD</div>
<div class="al-huge">并行 EVM</div>
<div>高性能并行 EVM 面向 Agent 微任务的高频结算方向；目标 eip155:<b>10143</b> Testnet</div>
</div>

<div class="al-arrow">→</div>

<div class="al-step">
<div class="al-none">402 · X402</div>
<div class="al-huge">HTTP 付费意图</div>
<div>EIP-3009 签名 → USDC Transfer / nonce 观察；签名意图、预留与回执分离，不依赖同步 ACK</div>
</div>

<div class="al-arrow">→</div>

<div class="al-step">
<div class="al-none">✓ · AGENTLEDGER</div>
<div class="al-huge">Proof 层</div>
<div>只读 observe / verify / reconcile；不依赖特定 Agent 框架，任何 Runtime 都可挂接</div>
</div>

</div>

<div class="al-bar">不展示未证实的 TPS / 最终性 / 已付款指标；展示的是协议边界、可复核事件与安全失败路径</div>

---
layout: default
---

<div class="al-title">现场 2 分钟：先演产品线，再亮反例</div>

<div class="al-timeline">

<div class="al-q"><div class="al-num">0:00–0:25 · Pocket / Created</div><div>打开任务，展示 taskId、descriptionHash、chain ID。</div></div>

<div class="al-q"><div class="al-num">0:25–0:50 · Recorder Paid</div><div>展示合约声明 Paid；独立付款仍为 <b>PaymentUnknown</b>。</div></div>

<div class="al-q"><div class="al-num">0:50–1:20 · Evidence / Verify</div><div>选原始文件重算 SHA-256；改 1 字节即拒绝。</div></div>

<div class="al-q"><div class="al-num">1:20–2:00 · Lost ACK / Reject</div><div>不重扣；Studio 展示错身份 / 跨 Ledger 拒绝。</div></div>

</div>

<div class="al-grid2 mt6">

<div><div class="al-num">观众看到</div><div>Pocket 的任务 → 支付观察 → 证据验证完整旅程</div></div>
<div><div class="al-num">工程评委看到</div><div>专向 CI、Anvil 生命周期、负向测试、`summary.json` 与验收截图</div></div>

</div>

---
layout: default
---

<div class="al-title">验收矩阵：全部专项门禁</div>

| 验证项目 | 结果 |
| --- | --- |
| Pocket TypeScript + 单测（15 项） | ✅ PASS |
| Expo Web 静态构建（export:web） | ✅ PASS |
| Chromium 桌面 / 手机交互与截图 | ✅ PASS |
| SDK / Evidence / x402 / Foundry | ✅ PASS |
| Anvil 本地生命周期 | ✅ PASS |
| onsite:check 离线 readiness | ✅ PASS |
| 整合后全量回归 126 + 14 + 15 + 12 | ✅ PASS |
| GitHub Actions 门禁 CI（三 job 全固化） | ✅ 已接入 |
| Pocket 真实 Testnet 只读验收（test:browser:live） | ✅ PASS — 真实链 Created / PaymentUnknown，verify 灰显，summary.json 保持 TESTNET_TASK_READ_ONLY |
| 工作台 Studio 真实 Testnet 只读链路 | ✅ 本机实测 |
| **真实 Monad Testnet 付款验收** | ⛔ **NOT COMPLETE — 刻意未完成** |
| 原生 iOS 构建 / 实机测试 | ⏳ Pending |

<div class="al-bar amber">链上 Paid / Proven 不会被自动当作独立 USDC 支付证明 · 本项目在 dev 独立迭代；公网付费验收仍被阻断</div>

---
layout: default
---

<div class="al-title">边界与诚实局限</div>

<div class="al-grid2">

<div class="al-q">
<div class="al-num">已经做到（本地、可复现）</div>
<ul>
<li>可复现的 Pocket / Studio 旅程与真实 SHA-256 核验</li>
<li>只读 Testnet 任务观察 + 通过 verifier 的显式 CORS（Pocket `test:browser:live` 已跑通真实链：Created / PaymentUnknown）</li>
<li>fail-closed：Lost ACK / 跨合约 / 未审核部署全部处理</li>
<li>offline 预检 + 评审面板输入（ready 恒 false 的公网验收标志）</li>
</ul>
</div>

<div class="al-q amber">
<div class="al-num">明确未完成</div>
<ul>
<li>真实 Monad Testnet 付款验收（需审核过的部署 + 真实 USDC + 独立对账）</li>
<li>不可逆最终性、独立执行正确性证明</li>
<li>原生 iOS 构建与实机文件互操作测试</li>
<li>SDK 保持 private 预览；不发布、不嵌入 RN 重试验意图机</li>
<li>手机二维码连接未实现；真机需 HTTPS 远端服务</li>
</ul>
</div>

</div>

<div class="al-bar">Hackathon prototype · NOT production-ready</div>

---
layout: default
---

<div class="al-title">下一步：把信任从承诺变成证据</div>

<div class="al-flow">

<div class="al-step"><div class="al-none">01</div><div class="al-huge">Reviewed Deployment</div><div>PR 审核注册表 + 可信 Recorder</div></div>
<div class="al-arrow">→</div>
<div class="al-step"><div class="al-none">02</div><div class="al-huge">Real Testnet USDC</div><div>专用钱包真付 + EIP-3009 意图与预留</div></div>
<div class="al-arrow">→</div>
<div class="al-step"><div class="al-none">03</div><div class="al-huge">Independent Reconcile</div><div>只读对账（Transfer / nonce / precharge / 链态一致）</div></div>
<div class="al-arrow">→</div>
<div class="al-step"><div class="al-none">04</div><div class="al-huge">Audited Evidence</div><div>不可变证据锚点与独立审计</div></div>

</div>

<div class="al-grid2 mt6">

<div><div class="al-num">产物</div><div>Pocket · Studio · Read-only SDK · readiness/summary 工具</div></div>
<div><div class="al-num">仓库</div><div>github.com/avaargsh/agent-control-plane · <b>dev</b></div></div>

</div>

---
layout: default
class: text-center
---

<div class="al-cover">

## 让信任从承诺变成证据

**Pay · Execute · Prove**

<div class="mt6">

```bash
git clone https://github.com/avaargsh/agent-control-plane
npm ci --ignore-scripts --no-audit --no-fund
npm run dev      # Studio 4321 · Testnet 工作台 4322 · Pocket 8081
```

</div>

感谢聆听——所有被声称的，都在别处被独立核验。
</div>
