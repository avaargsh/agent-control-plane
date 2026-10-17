export const DEMO_STEPS = Object.freeze([
  { title: '登记任务', action: 'created', text: '输入文本的 SHA-256 与任务 ID 一起登记。当前是本地模拟；真实测试网任务可在最后一步单独读取。' },
  { title: '观察支付声明', action: 'paid', text: 'Recorder 可以声明 Paid，但 USDC Transfer 和授权 nonce 尚未被独立核对，所以支付观察仍是 PaymentUnknown。' },
  { title: '查看本地工具结果', action: 'proven', text: '浏览器用确定性文本统计工具生成输出，并计算 Evidence 哈希。这里的账本锚点是模拟数据，不是链上交易或 ACP 执行证明。' },
  { title: '重新计算证据', action: 'verify', text: '从原始输入、输出和规范化 Evidence JSON 重新计算 SHA-256。匹配证明字节一致，不会升级支付结论。' },
  { title: '尝试篡改输出', action: 'tamper', text: '修改一个输出字节，再用相同 Evidence 验证。校验必须失败；恢复原样本后可以重新验证。' },
  { title: '演示响应丢失', action: 'lost-ack', text: '没有收到 ACK 不能证明未扣款。保留 PaymentUnknown，不重新支付，也不伪造收据。' },
  { title: '读取真实测试网任务', action: 'live', text: '读取已部署合约中的真实任务。每次都重新查询 RPC；当前链上任务与前面的本地模拟是独立的数据源。' },
]);

export function validShowcase(value) {
  return value?.schemaVersion === 'agentledger.demo-showcase.v1' && value.chainId === 10143
    && /^0x[0-9a-fA-F]{40}$/.test(value.contract || '') && /^0x[0-9a-fA-F]{64}$/.test(value.taskId || '')
    && /^0x[0-9a-fA-F]{64}$/.test(value.creationTransaction || '');
}
