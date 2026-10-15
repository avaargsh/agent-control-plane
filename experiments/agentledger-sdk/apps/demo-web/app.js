/* ========================================================================
   AgentLedger Demo Web — interaction
   Local simulation only: no transactions, no wallets, no network calls.
   ======================================================================== */

const STAGES = [
  { id: 'created', label: 'Created' },
  { id: 'payment-unknown', label: 'PaymentUnknown' },
  { id: 'payment-observed', label: 'PaymentObserved' },
  { id: 'payment-recorded', label: 'PaymentRecorded' },
  { id: 'evidence-anchored', label: 'EvidenceAnchored' },
  { id: 'conflict', label: 'Conflict' },
];

// 模拟演示场景：预设 taskId 与对应阶段，点击即可快速体验
const SCENES = [
  { label: 'Created', taskId: '0xcafe111122223333444455556666777788889999aaaabbbbccccddddeeeeffff', stage: 'created' },
  { label: 'PaymentUnknown', taskId: '0xdead111122223333444455556666777788889999aaaabbbbccccddddeeeeffff', stage: 'payment-unknown' },
  { label: 'PaymentObserved', taskId: '0xbeef111122223333444455556666777788889999aaaabbbbccccddddeeeeffff', stage: 'payment-observed' },
  { label: 'PaymentRecorded', taskId: '0x1234111122223333444455556666777788889999aaaabbbbccccddddeeeeffff', stage: 'payment-recorded' },
  { label: 'EvidenceAnchored', taskId: '0xabcd111122223333444455556666777788889999aaaabbbbccccddddeeeeffff', stage: 'evidence-anchored' },
  { label: 'Conflict', taskId: '0xffff111122223333444455556666777788889999aaaabbbbccccddddeeeeffff', stage: 'conflict' },
];

// 每个阶段下应独立核查的支付事实与证据事实（read-only 观察，不下结论）
const FACTS = {
  created: {
    title: 'Created — 任务已创建，尚无付款证明',
    chip: 'Created',
    payment: [
      { label: '链上任务状态', value: 'getTask(taskId) → status = Created' },
      { label: 'ACP 声明', value: '权威 ACP NotStarted（由集成方提供；绝不从缺失本地文件推断）' },
    ],
    evidence: [
      { label: '结论', value: '未知任何授权的支出；Ledger 自身从不授权支出' },
    ],
    note: '只读观察；不发送任何交易，不签名，不调用 recordPayment。',
  },
  'payment-unknown': {
    title: 'PaymentUnknown — 结果不确定，保持原样',
    chip: 'PaymentUnknown',
    payment: [
      { label: 'x402 尝试', value: '首次授权尝试已预留；HTTP ACK 可能已丢失' },
      { label: '链上记录', value: '链上显示 Paid 但未独立核验 USDC 转账 → 不可视为已收款' },
    ],
    evidence: [
      { label: '原则', value: '结果不确定 ≠ 支付失败/未付款；不自动充值，不重发签名，不复用 nonce' },
    ],
    note: '等待核查，或由 ACP / 人工决定是否恢复。仅读取 RPC / 交易回据 / 本地预付日志。',
  },
  'payment-observed': {
    title: 'PaymentObserved — 支付事件已独立观察',
    chip: 'PaymentObserved',
    payment: [
      { label: '支付方报价', value: '签署的付款方报价 + 预付预留' },
      { label: '链上事件', value: '匹配的 USDC Transfer（payer/payee/amount 精确一致） + EIP-3009 AuthorizationUsed nonce，同一笔交易' },
      { label: '链上任务', value: 'getTask(taskId) 仍为 Created，记录器尚未确认' },
    ],
    evidence: [
      { label: '结论', value: '支付事件已独立观察；等待链上可信记录器确认匹配' },
    ],
    note: '进入 PaymentRecorded 需 recorder 链上 Paid 与观察到的事件匹配。',
  },
  'payment-recorded': {
    title: 'PaymentRecorded — 支付记录与观察一致',
    chip: 'PaymentRecorded',
    payment: [
      { label: '独立核查', value: '经独立核查的支付事件' },
      { label: '记录器确认', value: '匹配的可信记录器链上 Paid / Proven 状态与结算引用' },
    ],
    evidence: [
      { label: '结论', value: '支付记录与观察到的交易匹配；执行结果仍由 ACP 业务流程拥有' },
    ],
    note: '决定是执行、退款还是重试，由 ACP / 业务流程决定，Ledger 不参与。',
  },
  'evidence-anchored': {
    title: 'EvidenceAnchored — 证据摘要与链上 anchor 匹配',
    chip: 'EvidenceAnchored',
    payment: [
      { label: '前置条件', value: 'PaymentRecorded 条件全部满足' },
      { label: '链上状态', value: '链上 Proven 状态与 Evidence digest 已锚定' },
    ],
    evidence: [
      { label: '核查方法', value: '规范 agentledger.evidence.v1 字节重新计算 SHA-256，与链上 anchor 比对' },
      { label: '结论', value: '摘要一致；仅证明“哪条摘要被记录了”，不证明执行正确或业务成功' },
    ],
    note: 'ACP 需单独核查 independent-execution-proof.json 与业务 OutcomeContract。',
  },
  conflict: {
    title: 'Conflict — 观察到的不一致，失败关闭',
    chip: 'Conflict',
    payment: [
      { label: '冲突来源', value: '结算引用、付款方、金额、签署报价或上传的 Evidence 摘要与链上状态不一致' },
    ],
    evidence: [
      { label: '处置', value: '调查 / 对账；绝不降级回 Created，不充值' },
    ],
    note: 'Conflict 不是终端，但在未解决前不得自动重试任何金融副作用。',
  },
};

// 验证详情面板数据：每项带状态徽章（ok / conflict / null）
const VERIFY = {
  created: {
    title: 'Created — 阶段详情',
    chip: 'Created',
    payment: [
      { label: '链上任务状态', value: 'getTask(taskId) → status = Created', status: 'ok' },
      { label: 'ACP 声明', value: '权威 ACP NotStarted（由集成方提供）', status: null },
    ],
    evidence: [
      { label: '结论', value: '未知任何授权的支出；Ledger 自身从不授权支出', status: null },
    ],
    hash: null,
    note: '只读观察；不发送任何交易，不签名，不调用 recordPayment。',
  },
  'payment-unknown': {
    title: 'PaymentUnknown — 阶段详情',
    chip: 'PaymentUnknown',
    payment: [
      { label: 'x402 尝试', value: '首次授权尝试已预留；HTTP ACK 可能已丢失', status: null },
      { label: '链上记录', value: '链上显示 Paid 但未独立核验 USDC 转账', status: 'conflict' },
    ],
    evidence: [
      { label: '原则', value: '结果不确定 ≠ 支付失败；不自动充值，不重发签名，不复用 nonce', status: null },
    ],
    hash: null,
    note: '等待核查，或由 ACP / 人工决定是否恢复。仅读取 RPC / 交易回据 / 本地预付日志。',
  },
  'payment-observed': {
    title: 'PaymentObserved — 阶段详情',
    chip: 'PaymentObserved',
    payment: [
      { label: '支付方报价', value: '签署的付款方报价 + 预付预留', status: 'ok' },
      { label: 'USDC Transfer', value: 'payer/payee/amount 精确一致，同一笔交易', status: 'ok' },
      { label: 'AuthorizationUsed nonce', value: 'EIP-3009 nonce 与报价匹配', status: 'ok' },
      { label: '链上任务状态', value: 'getTask(taskId) 仍为 Created', status: null },
    ],
    evidence: [
      { label: '结论', value: '支付事件已独立观察；等待记录器确认匹配', status: null },
    ],
    hash: null,
    note: '进入 PaymentRecorded 需 recorder 链上 Paid 与观察到的事件匹配。',
  },
  'payment-recorded': {
    title: 'PaymentRecorded — 阶段详情',
    chip: 'PaymentRecorded',
    payment: [
      { label: '独立核查', value: '经独立核查的支付事件', status: 'ok' },
      { label: '记录器确认', value: '可信记录器链上 Paid 与结算引用匹配', status: 'ok' },
    ],
    evidence: [
      { label: '结论', value: '支付记录与观察到的交易匹配；执行结果由 ACP 拥有', status: null },
    ],
    hash: null,
    note: '决定执行 / 退款 / 重试由 ACP / 业务流程决定，Ledger 不参与。',
  },
  'evidence-anchored': {
    title: 'EvidenceAnchored — 阶段详情',
    chip: 'EvidenceAnchored',
    payment: [
      { label: '前置条件', value: 'PaymentRecorded 条件全部满足', status: 'ok' },
      { label: '链上状态', value: 'Proven 状态与 Evidence digest 已锚定', status: 'ok' },
    ],
    evidence: [
      { label: '核查方法', value: 'agentledger.evidence.v1 字节重新计算 SHA-256', status: 'ok' },
      { label: '结论', value: '摘要一致；仅证明“哪条摘要被记录了”', status: null },
    ],
    hash: {
      local: '0x9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
      chain: '0x9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
      match: true,
    },
    note: 'ACP 需单独核查 independent-execution-proof.json 与业务 OutcomeContract。',
  },
  conflict: {
    title: 'Conflict — 阶段详情',
    chip: 'Conflict',
    payment: [
      { label: '冲突来源', value: '结算引用 / 付款方 / 金额与链上状态不一致', status: 'conflict' },
    ],
    evidence: [
      { label: '处置', value: '调查 / 对账；绝不降级回 Created，不充值', status: null },
    ],
    hash: {
      local: '0xabc123def4567890...',
      chain: '0xdef456abc1237890...',
      match: false,
    },
    note: 'Conflict 不是终端，但在未解决前不得自动重试任何金融副作用。',
  },
};

// DOM 元素
const taskIdInput = document.getElementById('taskId');
const stageBtns = document.getElementById('stageBtns');
const sceneBtns = document.getElementById('sceneBtns');
const factsEl = document.getElementById('facts');
const hintEl = document.getElementById('hint');
const tlItems = document.querySelectorAll('.tl-item');

// 验证详情面板元素
const verifyPanel = document.getElementById('verifyPanel');
const verifyTitle = document.getElementById('verifyTitle');
const verifyChip = document.getElementById('verifyChip');
const verifyPayment = document.getElementById('verifyPayment');
const verifyEvidence = document.getElementById('verifyEvidence');
const verifyHash = document.getElementById('verifyHash');
const hashLocal = document.getElementById('hashLocal');
const hashChain = document.getElementById('hashChain');
const hashStatus = document.getElementById('hashStatus');
const verifyNote = document.getElementById('verifyNote');

let activeStageId = 'payment-unknown';

// 把 Stage id 映射到时间轴上的顺序索引
const TL_ORDER = ['created', 'payment-unknown', 'payment-observed', 'payment-recorded', 'evidence-anchored'];

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// 全局：边栏导航点击
window.goStage = function (id) {
  activeStageId = id;
  // 同步左侧边栏高亮
  document.querySelectorAll('.sidebar .nav[data-stage]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.stage === id);
  });
  renderStageButtons();
  renderSceneButtons();
  renderTimeline();
  renderFacts();
  renderVerify();
};

// 场景快捷按钮点击
window.goScene = function (taskId, stage) {
  taskIdInput.value = taskId;
  goStage(stage);
};

function renderStageButtons() {
  stageBtns.innerHTML = '';
  STAGES.forEach((s) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `btn-stage ${s.id === activeStageId ? 'active' : ''}`;
    btn.textContent = s.label;
    btn.setAttribute('aria-pressed', s.id === activeStageId);
    btn.addEventListener('click', () => goStage(s.id));
    stageBtns.appendChild(btn);
  });
}

function renderSceneButtons() {
  sceneBtns.innerHTML = '';
  const title = document.createElement('p');
  title.style.cssText = 'width:100%;font-family: var(--font-mono); font-size: 11px; color: var(--muted); margin: 0 0 0.5rem 0; letter-spacing: 0.2px;';
  title.textContent = '快速体验模拟场景';
  sceneBtns.appendChild(title);

  SCENES.forEach((sc) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `btn-stage ${sc.stage === activeStageId ? 'active' : ''}`;
    btn.textContent = sc.label;
    btn.setAttribute('aria-pressed', sc.stage === activeStageId);
    btn.addEventListener('click', () => goScene(sc.taskId, sc.stage));
    sceneBtns.appendChild(btn);
  });
}

function renderTimeline() {
  tlItems.forEach((item, i) => {
    const id = TL_ORDER[i];
    item.classList.remove('done', 'active', 'conflict');
    if (id === activeStageId) {
      item.classList.add(activeStageId === 'conflict' ? 'conflict' : 'active');
    } else if (TL_ORDER.indexOf(activeStageId) > i) {
      item.classList.add('done');
    }
  });
}

function renderItemHtml(items) {
  return items
    .map((it) => {
      const statusClass = it.status ? `verify-item ${it.status}` : 'verify-item';
      const statusHtml = it.status
        ? `<span class="item-status">${it.status === 'ok' ? '已核验' : '冲突'}</span>`
        : '';
      return `
        <div class="${statusClass}">
          <div class="item-label">${escapeHtml(it.label)}</div>
          <div class="item-value">${escapeHtml(it.value)}</div>
          ${statusHtml}
        </div>
      `;
    })
    .join('');
}

function renderFacts() {
  const f = FACTS[activeStageId];
  const taskId = taskIdInput.value.trim() || '(未填写)';
  const shortId = taskId.length > 18 ? taskId.slice(0, 10) + '…' + taskId.slice(-6) : taskId;

  const paymentHtml = f.payment
    .map((p) => `<li><strong>${escapeHtml(p.label)}：</strong>${escapeHtml(p.value)}</li>`)
    .join('');
  const evidenceHtml = f.evidence
    .map((e) => `<li><strong>${escapeHtml(e.label)}：</strong>${escapeHtml(e.value)}</li>`)
    .join('');

  const isAnchor = activeStageId === 'evidence-anchored';
  const isConflict = activeStageId === 'conflict';

  factsEl.innerHTML = `
    <div class="fact ${isAnchor ? 'anchor' : ''} ${isConflict ? 'conflict' : ''}">
      <div class="fact-head">
        <h4>${escapeHtml(f.title)} <span class="fact-chip">${escapeHtml(f.chip)}</span></h4>
      </div>
      <div class="fact-body">
        <p>关注任务 <code class="hash" id="factTaskId">0x…</code> 的独立核查清单：</p>
        <ul style="margin:0.4rem 0 0.5rem 1.2rem; color: var(--muted);">${paymentHtml}</ul>
        <ul style="margin:0.4rem 0 0.5rem 1.2rem; color: var(--muted);">${evidenceHtml}</ul>
        <p class="note">${escapeHtml(f.note)}</p>
      </div>
    </div>
  `;
  hintEl.textContent = `当前阶段：${f.title}（taskId：${escapeHtml(shortId)}）`;
  document.getElementById('factTaskId').textContent = escapeHtml(taskId);
}

function renderVerify() {
  const v = VERIFY[activeStageId];
  const taskId = taskIdInput.value.trim() || '(未填写)';
  const shortId = taskId.length > 18 ? taskId.slice(0, 10) + '…' + taskId.slice(-6) : taskId;

  verifyTitle.textContent = v.title;
  verifyChip.textContent = v.chip;
  verifyChip.className = `verify-chip ${activeStageId === 'conflict' ? 'conflict' : ''}`;
  verifyPayment.innerHTML = renderItemHtml(v.payment);
  verifyEvidence.innerHTML = renderItemHtml(v.evidence);
  verifyNote.textContent = v.note;

  if (v.hash) {
    verifyHash.style.display = 'block';
    hashLocal.textContent = v.hash.local;
    hashChain.textContent = v.hash.chain;
    hashStatus.textContent = v.hash.match ? '匹配' : '不匹配';
    hashStatus.className = `hash-match ${v.hash.match ? 'match' : 'mismatch'}`;
  } else {
    verifyHash.style.display = 'none';
  }
}

function setStage(id) {
  goStage(id);
}

taskIdInput.addEventListener('input', () => {
  renderFacts();
  renderVerify();
  renderSceneButtons();
});

// 初始化：统一走 goStage，确保边栏、时间轴、按钮高亮一致
goStage(activeStageId);
