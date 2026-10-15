# AgentLedger Demo Web

Monad 测试网（chainId 10143）“支付 → 执行 → 证明”只读演示观察台。

## 使用方法

```bash
cd apps/demo-web
# 直接用浏览器打开 index.html 即可，无需构建步骤
# 或启动本地静态服务：
python3 -m http.server 8080
# 然后访问 http://localhost:8080/
```

## 页面结构

页面采用标题栏 + 左侧边栏 + 主内容区布局（参考 BioAI Workstation 视觉风格）：

1. **Hero** — 介绍 Pay → Execute → Prove 三阶段，说明只读观察、不托管资金、不主网；下方展示阶段数、网络、代币、写入权限四项关键指标。
2. **三阶段卡片** — Pay（USDC + EIP-3009）、Execute（ACP 决策）、Prove（SHA-256 证据锚点）。
3. **状态机时间轴** — Created → PaymentUnknown → PaymentObserved → PaymentRecorded → EvidenceAnchored，以及 Conflict 分支；左侧边栏可直接跳转任意阶段。
4. **演示检查器** — 输入 `taskId`，或点击“快速体验模拟场景”快速切换阶段；右侧实时展示该阶段下应独立核查的支付事实与证据事实。
5. **验证详情面板** — 带状态徽章（已核验 / 冲突）的事实项，以及哈希比对行（本地 SHA-256 vs 链上 anchor，匹配 / 不匹配）。
6. **治理声明页脚** — 实验性、非 ACP v0.1、非生产就绪；只读函数不发送交易、不签名、不自动写入。

## 设计说明

- **配色**：浅灰背景 `#f5f6f8`、白卡片 `#ffffff`、细边框 `#e8eaee`，主色链条蓝 `#0f6b9e`（Pay/Execute）、证据紫 `#6d28d9`（Prove/锚点）；状态辅以验证绿/警示橙/冲突红。
- **字体**：标题衬线体 `Source Serif 4`（账本质感）、正文系统无衬线、哈希/地址 `IBM Plex Mono`。
- **组件**：卡片（12px 圆角、1px 边框）、主按钮/边框按钮/幽灵按钮、状态标签（tag）、指标卡（大数字）、进度条、英雄区块（渐变背景左右布局）。
- **交互**：原生 HTML/CSS/JS，无构建依赖；本地模拟，不发送任何交易，不连接真实钱包，无网络请求。
- **可访问性**：键盘焦点可见、尊重 `prefers-reduced-motion`、响应式适配移动端（边栏收缩、网格单列）。

## 与 SDK 的关系

本演示站是 `@agentledger/*` SDK 的前端展示层，仅用于演示 read-only 投影和阶段状态机语义，不包含真实的链上读取逻辑。如需接入 Monad 测试网 RPC，可在 `app.js` 的检查器部分扩展为真实的 SDK 调用。
