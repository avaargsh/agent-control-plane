# AgentLedger — Monad Blitz 上海 · 手动提交交接单

**活动**：Monad Blitz 上海，2026-10-17（[MOJO 活动页面](https://mojo.devnads.com/events/17)）。公开日程注明 **18:30（北京时间）项目提交截止**、19:00–20:00 展示；登录、现场签到和项目提交资格由 MOJO 平台控制，不能由代码代替。**本仓库不会自动推送至最终提交仓库，也不会自动操作 MOJO。**

## 项目介绍（可直接用于 MOJO，提交前复核）

**名称：AgentLedger — Pay → Execute → Prove**

AgentLedger 展示 Agent 任务的**创建事实、支付观察与证据字节一致性**怎样被分别验证。它以 Monad Testnet 上的任务账本记录身份和状态，通过浏览器本地生成/验证 SHA-256 证据，并在付款 ACK 丢失时保留 `PaymentUnknown`，不把“未知”误判为“未付款”从而二次扣费。

评委可以在 **/demo** 无钱包完成六步离线演示（Created → Recorder Paid claim → 本地文本工具 → Evidence verify → 篡改被拒绝 → Lost ACK / NO RECHARGE），可选第七步才会进行真正的 **Testnet 只读查询**。Pocket 提供四阶段移动/网页展示；Verification Studio 提供源事实、哈希与 `summary.json` 导出。真实链上已能只读核对 **Created** 任务，但**并未证明真实 USDC 支付闭环**。

**当前公开验收边界：** 本地演示和 Anvil 回归可验证；`phase0/reviewed-testnet-deployments.json` 尚无审核条目，真实 Monad Testnet USDC 付款验收为 **BLOCKED_NO_REVIEWED_DEPLOYMENT**。合约 Paid 是 Recorder 声明，Proven 是摘要锚定；字节哈希相同不代表执行正确或支付最终确定。

## 演示入口（请在现场本机执行）

| 入口 | 操作 | 权限与网络 |
| --- | --- | --- |
| Studio 3 分钟导览 | `npm run dev` 后打开 `http://127.0.0.1:4322/demo` | 前六步离线，无钱包 |
| 真实任务核对 | 导览第七步或 `/studio` 切换到 Testnet | Monad RPC，只读；失败时不回退模拟成功 |
| Wallet 工作台 | `http://127.0.0.1:4322/` | **可选** MetaMask 逐笔签名，只创建 Testnet 任务；不进行支付 |
| Pocket Web | `cd apps/pocket && npm ci --ignore-scripts && npm run web` | 本地四步讲解；链上读取仍为单独操作 |
| 评委 7 页 Slidev | `cd slides && npm ci --ignore-scripts && npm run build:judge` | 静态构建；不需要钱包 |

开始前：`npm ci --ignore-scripts --no-audit --no-fund`、`npm run onsite:check`、`npm run submit:check`、`npm test`。Chromium/Anvil 全回归由 CI 运行；线下可运行 `npm run test:browser`。不要为了演示重复创建链上任务或重发失败/未知付款。

## 面向评委展示的独立可验证事实

1. **Task Identity**：chainId 10143 + ledger contract + taskId + descriptionHash，不能跨 Ledger 混淆。
2. **Payment**：Recorder Paid claim ≠ 真实 USDC Transfer 和 authorization/nonce 校验；丢 ACK 时 `PaymentUnknown`，不重扣。
3. **Evidence**：原始输入/输出 SHA-256 与 canonical Evidence 的比对；篡改 1 字节明确拒绝，但不代表 Agent 执行正确。
4. **Read-only Testnet**：每次使用 RPC 重新核对 TaskCreated 交易与任务状态；这不是 USDC 结算或状态根 Verified 的证明。
5. **Evidence Export**：`summary.json` 标示 `LOCAL_SIMULATION` 或 `TESTNET_TASK_READ_ONLY`、时间、来源与边界；不把模拟数据转成链上付款 PASS。

Monad 文档特别区分交易回执、`Finalized` 与异步执行后的 `Verified`（状态根最终验证）。面向支付场景，不能仅凭一个早期交易回执声称经济结算不可逆。详见官方 [Deployment Summary](https://docs.monad.xyz/developer-essentials/summary)。

## 资料与复用策略

- **活动 / 最终人工提交：** [MOJO](https://mojo.devnads.com/projects) · [上海 2026-10-17 活动](https://mojo.devnads.com/events/17)
- **可信网络配置：** [Monad Developer Essentials](https://docs.monad.xyz/developer-essentials) · [Monad Testnet](https://docs.monad.xyz/developer-essentials/testnet) · [官方 Faucet](https://faucet.monad.xyz)
- **开发参考：** [monad-developers](https://github.com/monad-developers)（示例需自行审计）
- **可选 AI 编码 Skill：** [MONSKILLS](https://skills.devnads.com)，属于第三方开发辅助，不是本项目运行时或供应链必需依赖；避免未经审核直接安装到参赛打包。
- **可选合约可观测/模拟：** [contract.dev](https://contract.dev) 提供外部监控、分析与 Stagenets 介绍；当前**未接入**，不能称已完成合约 Fork 验收。

网络参数来自 Monad 官方页面，**Testnet = 10143 / MON / `https://testnet-rpc.monad.xyz`**。不要将主网 143 或旧测试网资料误用到现场演示。

## 手动提交与安全交接

1. 先冻结经 CI 验收的开发仓库 commit，记录它的 SHA 和日期；保持原始开发仓库不变。
2. 在本机运行 `npm run submit:check`，须显示 `readyForManualHandoff: true`。此预检**只检查源码/静态材料与已跟踪敏感文件名**，不代替安全审计、浏览器实测或合约验收。
3. 确认 `slides/judge.md` 已重新构建，重新录制最新浏览器截图，保存所有验收产物原始哈希。需要时导出给评委，但不要把生成的 `out/` 或含地址/敏感信息的临时文件随源码提交。
4. 在单独的**最终提交仓库本地工作区**中人工复制**经过审核的项目源码/文档**；不要连同旧 `.git`、`.env`、钱包私钥、助记词、API key、`out/`、`node_modules/`、交易日志或个人信息复制。先 review diff，再手动 commit/push。
5. 在 MOJO 登录并使用本人已授权的项目提交流程填写项目名称、介绍、仓库链接、实际演示链接/截图、团队资料。**本项目不会自动提交 MOJO 或自动发布最终仓库。**
6. 若未完成经审核的真实 Testnet 部署/支付，请保留 `BLOCKED_NO_REVIEWED_DEPLOYMENT` 与 `PaymentUnknown`，不要改变文案冒充测试网 USDC 支付成功。

## 提交前失败即停

- 浏览器展示含旧开发仓库地址或预填最终仓库地址；
- `submit:check` 报缺文件、暴露敏感文件或公开页面源码链接；
- CI/Chromium/Forge/Anvil 未通过，或 Testnet RPC 本次查询不可用却仍显示绿色已验证；
- `summary.json` 来源与验证事实不一致；
- `PaymentUnknown` 被界面或脚本当作 Unpaid 自动重试。
