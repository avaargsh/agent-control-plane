# Pocket 应用真实测试网只读验收记录

> 本文件是对一次**已执行**验收的事实记录,不是新的许可或声明。
> 结论边界:`PASS_OBSERVED_CREATED_PAYMENT_UNKNOWN` — 只读观察到公开 Monad
> Testnet 任务,**不是** USDC 结算证明、不是执行正确性证明、不是 finality、
> 也不是 reviewed-deployment 公共验收。

## 1. 验收工具与命令

- 目标应用:`apps/pocket`(Expo Mobile / Web 只读观察端)。
- 命令:`apps/pocket` 下 `npm run test:browser:live`
  (脚本:`apps/pocket/browser/pocket-live-verifier.mjs`)。
- 依赖:先 `npm run export:web` 生成 `dist/`;脚本读取真实链上任务,
  但**不**签名、**不**广播交易、**不**写入 `phase0/reviewed-testnet-deployments.json`。

## 2. 真实对象(只读引用)

与 `phase0/demo-showcase.json` 一致:

| 项 | 值 |
| --- | --- |
| 链 | Monad Testnet,chain ID `10143` |
| 合约 | `0x2a497095a6f3b6675e251432d50060d0a6fc1b9c` |
| 部署交易 | `0x4565654b2e3a6f32329b005b0de779a6efb91806e1bd7a2582c7532ce3bcdfbe` |
| 创建交易 | `0x40146d20bedda88a4dbdb118aa1cdae421e32f3c77271744d6d0a60db3d675c6` |
| taskId | `0xd424d23731f1c390a22b1e7f9cafdbe84d1dc0753b714c391b735f1e9139fb0e` |
| RPC | `https://testnet-rpc.monad.xyz`(verifier 内固定) |

## 3. 本次执行的服务端独立对账(原始结果摘录)

验收脚本在启动浏览器前,先通过本地只读 verifier
(`GET /api/task?contract=…&taskId=…`)直接读取链上任务。本次
(2026-10-17T05:48:37.374Z)返回:

```json
{
  "status": "Created",
  "amountAtomic": "0",
  "maxBudgetAtomic": "1000",
  "descriptionHash": "0x00ccc63ef61375030b35f8514e5cbce9d4921a7f0733adbc8ba6e4c21f85912f",
  "evidenceHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
  "creator": "0x6404a1075d005f4501F9A6D7A3ca4755A210550f",
  "payer": "0x0000000000000000000000000000000000000000",
  "expectedPayment": "PaymentUnknown",
  "chargeAgain": false
}
```

语义核查:`Created` ≠ 付款;`amountAtomic=0`、`evidenceHash=0x000…0` 与
"未支付、无证据锚定" 的预期一致;支付状态保持 `PaymentUnknown`,
`chargeAgain=false`。

## 4. 浏览器链路(真实 verifier + 真实 RPC)

导出后的 `dist/` 由 `127.0.0.1:19006`(verifier 明确允许的 Pocket Web
CORS origin)提供,headless Chrome 中逐项断言:

- 输入真实合约与 taskId 后点击 *Observe Task · no gas*,应用经
  `GET /api/task` 只读代理返回真实任务,界面出现 `TASK OBSERVED ·
  PaymentUnknown (no USDC verification)` 与 `TESTNET · TASK OBSERVED`;
- Inspector 显示 `TESTNET TASK ONLY · PAYMENT UNKNOWN`、Chain ID
  `10143 (task read)`、Recorder 状态 `NotRecorded · NOT payment proof`;
  USDC Transfer 与 EIP-3009 nonce 均为 `not independently observed`;
- Evidence 页:任务为 `Created` 时校验按钮**禁用**、验证状态
  `NOT VERIFIED`(没有 Proven 锚点,禁止伪造"已验证") ;
- Payment 页:`PaymentUnknown` 保持,Recorder claim `NotRecorded`;
- **失金路径真实触发**:同一 taskId + 无代码的 Ledger 地址
  (`0x4242…4242`)→ verifier 真实返回 502 → 应用显示
  `TASK_READ_UNAVAILABLE_502` 并撤销先前观察上下文(回到
  LOCAL DEMONSTRATION),**不会**沿用旧任务的数据;
- Journal 导出 `summary.json`:逐字段断言为
  `mode=TESTNET_TASK_READ_ONLY`、`outcome=TASK_OBSERVED_NOT_PAYMENT_ACCEPTANCE`
  、`payment.observed=PaymentUnknown`、`chargeAgain=false`、
  `rpc.contacted=true`、`evidence.verification=NOT_VERIFIED`、
  `security.publicTestnetAcceptance=BLOCKED_NO_REVIEWED_DEPLOYMENT`、
  `realFundsUsed=false`;
- 390px 移动布局重新观察同一真实任务成功(桌面双栏→移动单栏会重挂载
  Phone 组件,状态须重新建立而非复用旧结果);verifier 收到的页面
  `/api/task` 请求恰好 4 次。

## 5. 产物

`npm run test:browser:live` 在 `out/pocket-live-testnet/` 写入:

- `pocket-live-acceptance.json` — 机密无关的验收记录
  (`schemaVersion: agentledger.pocket.live-acceptance.v1`,
  `finalityProven:false`、`serviceDeliveryProven:false`、
  `paymentAccepted:false`、`transactionsBroadcast:0`);
- `pocket-live-summary.json` — 浏览器实际下载的 summary(即上表);
- `pocket-live-tasks-observed.png` / `pocket-live-mobile.png` — 桌面与移动端真实观察截图。

## 6. 边界(不得夸大)

- 本验收只证明:部署、创建交易与当前链上任务可以被只读观察到,应用
  UI 在真实链路下保持"观察与验收分离"(`PaymentUnknown`、
  `chargeAgain=false`、`BLOCKED_NO_REVIEWED_DEPLOYMENT`)。
- 本验收**不**证明:USDC 到账、Recorder 诚实、Agent 执行正确、
  finality、或任何公共付费验收;`phase0/reviewed-testnet-deployments.json`
  仍为空(fail-closed),完整 live 验收仍按
  [live-testnet-observation](live-testnet-observation.md) 的门执行。
- 错误码 `TASK_READ_UNAVAILABLE_502` 是安全失败(无代码地址 / RPC
  异常统一返回),不区分"合约不存在"与"RPC 不可达"。
