# AgentLedger 现场演示与 Monad 官方联调（角色化交接）

> 目标：拿到一套能启动、能展示、能排错、不会误导观众的 AgentLedger Pocket。  
> 状态：**一个用户 App，工程 Studio 与 SDK 是支持工具，不是另外两个产品。**
> **没有经审核的 Monad Testnet 部署，不得宣称真实支付验收通过。**

## 0. 产品入口（不再同时演示三个 App）

| 使用者 | 唯一推荐入口 | 职责 |
|---|---|---|
| 现场观众 | `experiments/agentledger/apps/pocket` | Web/Expo 任务、支付观察、Evidence、活动记录 |
| 技术评审 | `experiments/agentledger/examples/verifier` | GET-only Task API、Verification Studio、模拟/Anvil 验收证据 |
| SDK 消费者 | `experiments/agentledger/sdk` | 内部私有只读 `observe / verify / reconcile`，不能签名付款 |
| 历史兼容 | `experiments/agentledger-sdk` | 保留已有 CLI/包的回归；不要开启第二套用户 UI |

查看 [应用归属表](application-map.md)。**Pocket 是现场主界面；Studio 是必要时才打开的技术验证台。**

## 1. 现场五分钟准备：先离线验收

需要 Node.js 22 或 24、Git、浏览器。准备两个终端（国内网络波动时建议先离线跑模拟，不依赖钱包或 RPC）。

```bash
cd experiments/agentledger/apps/pocket
npm install --ignore-scripts --no-audit --no-fund
npm run typecheck
npm test
npm run onsite:check
npm run web
```

`onsite:check` **默认不访问网络**。JSON 中检查单用户 App、Studio、SDK、reviewed registry 路径齐全，并且必须出现：

```json
{
  "mode": "LOCAL_ONLY_NO_NETWORK",
  "safeToDemo": true,
  "safeToClaimPublicTestnetAcceptance": false,
  "payment": {
    "state": "PaymentUnknown",
    "independentlyVerified": false,
    "chargeAgain": false
  }
}
```

也可把报告保存为文件（报告路径必须不存在，不覆盖旧证据）：

```bash
mkdir -p /tmp/agentledger-field
node ../../tools/onsite-preflight.mjs --output /tmp/agentledger-field/readiness.json
```

## 2. 现场十分钟演示（全部模拟）

1. 打开 Pocket：**Created**（Task ID / descriptionHash）、**Paid claim**（链上 Recorder 声明而非 USDC 独立验收）、**Evidence**（SHA-256 本地哈希）、**Lost ACK**（PaymentUnknown / no recharge）
2. Evidence 页选择本地 JSON；可选原始二进制输入/输出（不上传）。手动篡改一个字节，必须返回 **NOT VERIFIED**
3. 桌面右侧 Evidence/Inspector 观察 `summary.json`；导出后核对 `mode=LOCAL_SIMULATION`、`rpc.contacted=false`、`PaymentUnknown` 和部署审查阻断
4. 用工程命令演示跨 Ledger、错误付款人被拒绝：

```bash
cd experiments/agentledger/examples/verifier
npm install --ignore-scripts --no-audit --no-fund
node tools/demo-journey.mjs
# 同时可启动 npm start，打开本地 127.0.0.1:4321 的 Studio。
```

需要真实合约 Task 查询时，启动 `npm start` 后回 Pocket 在 Tasks 页面输入合约和 bytes32 Task ID。**这仍只是 task-only 查询；Chain `Paid/Proven` 不能推出 USDC 已付款。** 手机真机不能使用电脑的 `127.0.0.1` 访问本机服务；真机联调需要经授权的 HTTPS 服务。

## 3. 找 Monad 官方人员时，对照这几处权威资料

| 官方资料 | 现场要确认的问题 |
|---|---|
| [Monad Testnet 网络信息](https://docs.monad.xyz/developer-essentials/testnet) | 链 ID、RPC、测试网重置记录、Explorer、RPC 限流 |
| [Monad x402 官方指南](https://docs.monad.xyz/guides/x402) | facilitator 支持 **x402 v2 或更高**；Testnet USDC 地址、验证/结算错误语义 |
| [Monad 测试 MON 水龙头](https://faucet.monad.xyz) | 测试钱包 gas；**不要使用主网有资金的钱包** |
| [Circle 测试 USDC 水龙头](https://faucet.circle.com) | Monad Testnet 网络选择、Testnet USDC 可用性 |
| [Expo v57 Sharing](https://docs.expo.dev/versions/v57.0.0/sdk/sharing/) | iOS share sheet 与 Web 下载的区别 |
| [Expo DocumentPicker](https://docs.expo.dev/versions/latest/sdk/document-picker/) | `copyToCacheDirectory:true` 和必须从用户点击打开文件选择器 |

官方 x402 示例的 Testnet 参数（以当场官方文档为准）：

```text
chainId       = 10143
network       = eip155:10143
RPC           = https://testnet-rpc.monad.xyz/
USDC Testnet  = 0x534b2f3A21130d7a60830c2Df862319e593943A3
facilitator   = https://x402-facilitator.molandak.org
```

可选**只读联网**预检（只调用官方 RPC `eth_chainId`，Facilitator `GET /supported`，**不调用 /verify、/settle、签名或广播**）：

```bash
cd experiments/agentledger/apps/pocket
npm run onsite:network
```

**什么算通过：** RPC 需返回 `0x279f`（10143）；Facilitator 宣称 `eip155:10143` 的 `exact` Scheme。即使两项都是 PASS，仍只是“接口支持”，**不是一次成功付费**。网络不可用或声称其他网络时保持阻断；勿临时用主网地址“凑成功”。

## 4. 现场向技术评审与官方确认的四个问题

1. 当前 Monad Testnet 的 **x402 v2** 支持路径、`/supported` 响应格式、Testnet USDC `name/version` EIP-712 domain 是否仍和官方范例一致？
2. 因为 ACK 丢失而无法知道 `/settle` 是否处理成功时，**官方推荐按 transaction hash、nonce、Transfer 与 AuthorizationUsed 如何只读对账**？是否有可追踪的稳定 idempotency key？
3. 测试网合约在 2025-12-16 reset 之后应如何提交经过代码来源/字节码/Recorder 审核的部署证明？Explorer 如何比对创建交易和运行时 bytecode？
4. 需要提交什么最小可复核 evidence（chainId / contract / RPC / txHash / Transfer log index / nonce / Evidence SHA-256 / summary.json），才能区分 **观察成功、支付最终性、Agent 执行所有权**？

## 5. 真正的公开 Testnet 验收仍有哪些阻断

`phase0/reviewed-testnet-deployments.json` 目前未登记经审核的地址，所以：

- 当前只允许本地 **Anvil / simulated** 闭环；
- 没有审查的部署就必须输出 `PUBLIC_TESTNET_ACCEPTANCE_BLOCKED_NO_REVIEWED_DEPLOYMENT`；
- 不能把 `SIMULATED_HASH_MATCH`、合约的 `Paid` 或 Facilitator `/supported` 解释为真实 USDC 结算；
- **PaymentUnknown 不自动重试、不得重用 nonce、不得释放预算、不得绕过前置授权**。

真正的 **funded Testnet** 测试是独立、明确授权且使用专用测试钱包的下一项工作；不在此脚本中自动执行。必须先登记经审查的部署，再用只读验收 CLI 检查真实 Transfer、AuthorizationUsed、签名意图、precharge、Recorder、Evidence 与 canonical block reread。该 CLI 不会生成签名和交易。

## 6. 现场反馈模板

```text
AgentLedger Pocket / 现场场测
日期与环境：
Node / Expo / Browser / iOS/Android：
branch + commit SHA：
本地离线检查：PASS / FAIL（附 readiness.json）
模拟 Created→Paid claim→Evidence→Verify：PASS / FAIL（附 summary.json）
Lost ACK / 不重扣：PASS / FAIL
跨 Ledger / 错误身份拒绝：PASS / FAIL
真实文件 JSON / input / output：PASS / FAIL
官方 Testnet RPC chainId：MATCH / FAILED / NOT CHECKED
Facilitator /supported exact：ADVERTISED / NO / NOT CHECKED
reviewed deployment：BLOCKED / REVIEWED（须 PR 和地址证据）
真实支付与最终性：NOT ACCEPTED（除非另有真实验收材料）
问题：操作步骤 + 可复现输入（**不得包含私钥、签名原文、助记词**）
下一步责任人、需官方确认的具体问答：
```

现场结束后交付 **readiness.json + summary.json + 浏览器/移动端截图 + 官方答复链接**。缺少任何真实付款证据时，结论仍是 **模拟路径通过 / 公共支付验收待定**。
