// SPDX-License-Identifier: Apache-2.0
import { verifyEvidence } from './evidence-browser.mjs';
import { buildDemoCase } from './demo-scenarios.mjs';
import { makeSummary, RPC_LABEL } from './summary.mjs';
import { DEMO_STEPS, validShowcase } from './demo-guide.mjs';

const byId = id => document.getElementById(id);
const HEX40 = /^0x[0-9a-fA-F]{40}$/;
const HEX64 = /^0x[0-9a-fA-F]{64}$/;
const same = (a, b) => typeof a === 'string' && typeof b === 'string' && a.toLowerCase() === b.toLowerCase();
if (['/studio', '/demo'].includes(location.pathname)) document.querySelector('a.logo').href = location.pathname;
if (location.pathname === '/demo') document.title = 'AgentLedger · 3-minute guided demo';
const workspaceUrl = new URL('/', location.href);
if (location.port === '4321') workspaceUrl.port = '4322';
byId('wallet-workspace').href = workspaceUrl.href;
let currentMode = 'demo', currentDemo = null, loadedTask = null;
let lookupSequence = 0, verificationSequence = 0, evidenceVerification = 'NOT_RUN';
let liveAbort = null, observedAt = null, creationObservation = null;
let guideIndex = -1, guideBusy = false, guideGeneration = 0;
function text(id, value) { byId(id).textContent = String(value ?? '—'); }
function message(id, value, tone = '') {
  const element = byId(id); element.textContent = value; element.className = 'response' + (tone ? ' ' + tone : '');
}
function proofButtons(busy = false) {
  const proven = loadedTask?.status === 'Proven';
  byId('verify-button').disabled = busy || !proven;
  byId('run-sample-proof').disabled = busy || !proven || currentMode !== 'demo';
  byId('tamper-proof').disabled = busy || !proven || currentMode !== 'demo';
}
function displayTask(task, kind = 'SIMULATION') {
  loadedTask = task; evidenceVerification = 'NOT_RUN'; verificationSequence++;
  const live = kind === 'TESTNET';
  text('task-network', live ? 'Monad Testnet · 10143' : 'Local simulation · NOT CHAIN');
  for (const [id, field] of Object.entries({ 'task-value': 'taskId', 'task-description': 'descriptionHash', 'task-contract': 'contract', 'task-creator': 'creator', 'task-payer': 'payer', 'task-amount': 'amountAtomic', 'task-budget': 'maxBudgetAtomic', 'task-settlement': 'settlementRef', 'task-hash': 'evidenceHash' })) text(id, task[field]);
  text('task-rpc', live ? RPC_LABEL : 'No RPC contacted (simulated)');
  text('chain-state', task.status); text('chain-flag', live ? 'ON-CHAIN STATE' : 'SIMULATED');
  text('ledger-badge', task.status.toUpperCase());
  byId('ledger-badge').className = 'state-pill ' + (task.status === 'Proven' ? 'state-proven' : task.status === 'Paid' ? 'state-paid' : '');
  text('observation-title', task.status === 'Proven' ? 'Digest anchored' : task.status === 'Paid' ? 'Recorder-attested payment' : 'Created task');
  text('payment-fact', 'PaymentUnknown'); text('payment-transfer', 'NOT VERIFIED'); text('payment-nonce', 'NOT VERIFIED · nonce unavailable');
  text('payment-detail', task.status === 'Created' ? 'No verified payer payment attempt. Do not infer permission to spend.' : 'Recorder state alone does not verify USDC Transfer + EIP-3009 nonce.');
  text('evidence-fact', task.status === 'Proven' ? 'Hash anchored · bytes unchecked' : 'Not anchored');
  text('evidence-detail', task.status === 'Proven' ? 'Recompute canonical Evidence SHA-256 against this anchor.' : 'No independently verifiable Evidence anchor available.');
  byId('contract-link').hidden = !live;
  if (live) byId('contract-link').href = 'https://testnet.monadscan.com/address/' + task.contract;
  byId('save-summary').disabled = false;
  byId('fixture-downloads').hidden = live || task.status !== 'Proven';
  byId('live-share').hidden = !live;
  if (live) {
    const url = new URL(location.pathname, location.origin);
    url.hash = new URLSearchParams({ mode: 'live', contract: task.contract, taskId: task.taskId }).toString();
    byId('share-url').value = url.href;
    text('observed-at', '本次读取时间：' + new Date(observedAt).toLocaleString());
  }
  for (const id of ['evidence-file', 'input-file', 'output-file']) byId(id).value = '';
  proofButtons();
  message('verification-message', task.status === 'Proven' ? (live ? 'Choose original Evidence files to compare with chain hash.' : 'Verify the generated fixture or download its exact files.') : 'A Proven task is required before Evidence verification.');
}
function clearTask() {
  loadedTask = null; currentDemo = null; observedAt = null; creationObservation = null;
  evidenceVerification = 'NOT_RUN'; verificationSequence++;
  proofButtons();
  text('observation-title', 'No task selected'); text('ledger-badge', 'UNKNOWN');
  byId('ledger-badge').className = 'state-pill';
  text('chain-state', 'Not loaded'); text('chain-flag', 'NOT LOADED');
  text('task-network', 'No task loaded'); text('payment-fact', 'PaymentUnknown');
  text('payment-detail', 'No current task observation. No payment conclusion can be drawn.');
  text('evidence-fact', 'Not verified'); text('evidence-detail', 'No current Evidence anchor selected.');
  for (const id of ['task-value', 'task-description', 'task-contract', 'task-rpc', 'task-creator', 'task-payer', 'task-amount', 'task-budget', 'task-settlement', 'task-hash']) text(id, '—');
  text('payment-transfer', 'NOT VERIFIED'); text('payment-nonce', 'NOT VERIFIED');
  for (const id of ['contract-link', 'tool-preview', 'fixture-downloads', 'live-share', 'creation-transaction']) byId(id).hidden = true;
  byId('save-summary').disabled = true;
  message('verification-message', 'Load a Proven task to verify Evidence.');
}
function switchMode(mode) {
  lookupSequence++; liveAbort?.abort(); currentMode = mode;
  byId('network-indicator').dataset.state = 'configured';
  text('studio-network-status', 'Chain ID 10143 · not queried');
  byId('query-button').disabled = false;
  byId('demo-controls').hidden = mode !== 'demo'; byId('live-controls').hidden = mode !== 'live';
  for (const name of ['demo', 'live']) {
    byId('mode-' + name).classList.toggle('active', mode === name);
    byId('mode-' + name).setAttribute('aria-pressed', String(mode === name));
  }
  const strong = document.createElement('strong'), description = document.createElement('span');
  strong.textContent = mode === 'demo' ? 'LOCAL SIMULATION' : 'PUBLIC TESTNET STATE ONLY';
  description.textContent = mode === 'demo' ? 'Browser-local tool calculation and illustrative ledger data. No public transaction or USDC payment.' : 'Read-only chain query. Paid / Proven are Recorder and hash-anchor claims, NOT independently verified settlement.';
  byId('source-note').replaceChildren(strong, description); clearTask();
  text('guide-source', mode === 'demo' ? 'LOCAL SIMULATION · NO WALLET' : 'TESTNET · READ ONLY');
  if (mode === 'demo') message('showcase-message', '真实状态以本次 RPC 查询结果为准。');
  if (mode === 'demo') return showScenario('created');
}
async function showScenario(key) {
  if (currentMode !== 'demo') return false;
  const sequence = ++lookupSequence; verificationSequence++;
  const data = await buildDemoCase(key);
  if (currentMode !== 'demo' || sequence !== lookupSequence) return false;
  currentDemo = data;
  for (const button of document.querySelectorAll('[data-scenario]')) {
    const active = button.dataset.scenario === key;
    button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
  }
  text('scenario-heading', data.stage.heading); text('scenario-summary', data.stage.summary);
  displayTask(data.task); text('evidence-fact', data.evidenceFact);
  text('evidence-detail', data.task.status === 'Proven' ? 'SIMULATION ONLY · Reproduce the local digest from exact files.' : 'No independently checked input/output');
  byId('tool-preview').hidden = data.task.status !== 'Proven';
  for (const [id, field] of Object.entries({ 'tool-words': 'wordCount', 'tool-characters': 'characterCount', 'tool-bytes': 'byteCount', 'tool-lines': 'lineCount' })) text(id, data.toolResult[field]);
  text('tool-input', new TextDecoder().decode(data.inputBytes)); text('tool-output', new TextDecoder().decode(data.outputBytes));
  return true;
}
async function queryTask(contract, taskId, creationTransaction = null) {
  liveAbort?.abort(); const controller = new AbortController(); liveAbort = controller;
  clearTask(); const sequence = ++lookupSequence;
  if (!HEX40.test(contract) || !HEX64.test(taskId)) { message('query-message', 'Enter a valid contract address and bytes32 task ID.', 'error'); return false; }
  byId('query-button').disabled = true;
  message('query-message', 'Querying Monad Testnet RPC …');
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch('/api/task?' + new URLSearchParams({ contract, taskId }), { cache: 'no-store', signal: controller.signal });
    const task = await response.json();
    if (!response.ok) throw Error(task.error || 'Task lookup unavailable');
    if (task.chainId !== 10143 || !same(task.taskId, taskId) || !same(task.contract, contract)) throw Error('RPC task identity mismatch; no conclusion can be drawn');
    let creation = null;
    if (creationTransaction) {
      const witnessResponse = await fetch('/api/task-creation?' + new URLSearchParams({ contract, taskId, transactionHash: creationTransaction }), { cache: 'no-store', signal: controller.signal });
      creation = await witnessResponse.json();
      if (!witnessResponse.ok || creation.state !== 'CONFIRMED' || creation.chainId !== 10143 || !same(creation.transactionHash, creationTransaction) || !same(creation.taskId, taskId) || !same(creation.contract, contract) || !same(creation.task?.descriptionHash, task.descriptionHash) || !same(creation.from, task.creator)) throw Error('Creation transaction is not confirmed for this task');
    }
    if (sequence !== lookupSequence || currentMode !== 'live') return false;
    observedAt = new Date().toISOString(); creationObservation = creation;
    byId('network-indicator').dataset.state = 'verified';
    text('studio-network-status', 'Chain ID 10143 · RPC checked');
    displayTask(task, 'TESTNET');
    byId('creation-transaction').hidden = !creation;
    if (creation) byId('creation-transaction').href = 'https://testnet.monadscan.com/tx/' + creation.transactionHash;
    message('query-message', 'Chain state loaded. Payment proof remains unverified.', 'success');
    byId('observation-title').scrollIntoView({ behavior: 'auto', block: 'center' });
    return true;
  } catch (error) {
    if (sequence === lookupSequence && currentMode === 'live') {
      clearTask(); message('query-message', 'Lookup unavailable: ' + (error.name === 'AbortError' ? 'request cancelled or timed out; retry read-only lookup' : error.message), 'error');
      byId('network-indicator').dataset.state = 'unavailable';
      text('studio-network-status', 'Chain ID 10143 · lookup failed');
    }
    return false;
  } finally { clearTimeout(timeout); if (sequence === lookupSequence) byId('query-button').disabled = false; }
}
async function loadShowcase() {
  byId('load-showcase').disabled = true;
  switchMode('live'); const sequence = lookupSequence;
  message('showcase-message', '正在读取公开任务信息并重新核对 RPC…');
  try {
    const response = await fetch('/api/showcase', { cache: 'no-store', signal: AbortSignal.timeout(10000) });
    const reference = await response.json();
    if (!response.ok || !validShowcase(reference)) throw Error('真实任务入口不可用，请使用手动查询。');
    if (sequence !== lookupSequence || currentMode !== 'live') return false;
    byId('contract').value = reference.contract; byId('taskId').value = reference.taskId;
    const loaded = await queryTask(reference.contract, reference.taskId, reference.creationTransaction);
    if (currentMode !== 'live' || lookupSequence !== sequence + 1) return false;
    if (loaded) message('showcase-message', '本次链上状态：' + loadedTask.status + '；创建交易已核对，支付仍未验证。', 'success');
    else message('showcase-message', '本次未完成链上核对，请查看查询提示。不会回退为模拟成功。', 'error');
    return loaded;
  } catch (error) {
    if (currentMode === 'live' && lookupSequence === sequence) message('showcase-message', error.message, 'error');
    return false;
  }
  finally { byId('load-showcase').disabled = false; }
}
function verificationContext() {
  return { task: loadedTask, mode: currentMode, lookup: lookupSequence, attempt: ++verificationSequence };
}
function isCurrent(context) {
  return context.task === loadedTask && context.mode === currentMode && context.lookup === lookupSequence && context.attempt === verificationSequence;
}
async function checkEvidence(evidence, input, output, context) {
  if (!isCurrent(context)) return false;
  if (context.task?.status !== 'Proven') throw Error('No Proven task');
  const result = await verifyEvidence(evidence, context.task, input, output);
  if (!isCurrent(context)) return false;
  evidenceVerification = 'HASH_MATCHED';
  text('evidence-fact', context.mode === 'demo' ? 'SIMULATED · Hash matched' : 'Hash matched to chain anchor');
  text('evidence-detail', context.mode === 'demo' ? 'Local-only simulated anchor, not a Monad transaction.' : 'Canonical Evidence bytes match on-chain hash; payment remains unknown.');
  message('verification-message', (context.mode === 'demo' ? 'SIMULATED MATCH' : 'HASH MATCH') + ': ' + result.digest + '. Original input ' + (result.inputChecked ? 'verified' : 'not supplied') + '; output ' + (result.outputChecked ? 'verified' : 'not supplied') + '. No payment or execution correctness claim.', 'success');
  return true;
}
function verificationFailure(error, context, tamper = false) {
  if (!isCurrent(context)) return;
  evidenceVerification = 'NOT_VERIFIED'; text('evidence-fact', tamper ? 'SIMULATED · Tampered output rejected' : 'Not verified');
  text('evidence-detail', error.message);
  message('verification-message', (tamper ? 'EXPECTED REJECTION: ' : 'NOT VERIFIED: ') + error.message, tamper ? 'success' : 'error');
}
async function runSampleProof(tamper = false) {
  if (currentMode !== 'demo' || currentDemo?.task.status !== 'Proven') return false;
  const context = verificationContext(), sample = currentDemo;
  proofButtons(true); evidenceVerification = 'NOT_RUN';
  message('verification-message', tamper ? 'Changing one output byte before checking…' : 'Hashing local input, output and Evidence…');
  try {
    const output = sample.outputBytes.slice(); if (tamper) output[0] ^= 1;
    const matched = await checkEvidence(sample.evidence, sample.inputBytes, output, context);
    if (matched && tamper) throw Error('Tampered output unexpectedly passed');
    return matched;
  } catch (error) {
    verificationFailure(error, context, tamper);
    return tamper && isCurrent(context) && /Output bytes SHA-256 mismatch/.test(error.message);
  } finally { if (isCurrent(context)) proofButtons(); }
}
async function chosenFile(id, maxBytes) {
  const file = byId(id).files?.[0]; if (!file) return null;
  if (file.size > maxBytes) throw Error('File exceeds size limit: ' + file.name);
  return file.arrayBuffer();
}
byId('verify-button').addEventListener('click', async () => {
  if (loadedTask?.status !== 'Proven') return;
  cancelGuide(); const context = verificationContext(); proofButtons(true); evidenceVerification = 'NOT_RUN';
  message('verification-message', 'Computing SHA-256 entirely in this browser …');
  try {
    const bytes = await chosenFile('evidence-file', 2 * 1024 * 1024);
    if (!bytes) throw Error('Select an Evidence JSON file');
    const evidence = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    const input = await chosenFile('input-file', 10 * 1024 * 1024), output = await chosenFile('output-file', 10 * 1024 * 1024);
    await checkEvidence(evidence, input, output, context);
  } catch (error) { verificationFailure(error, context); }
  finally { if (isCurrent(context)) proofButtons(); }
});
function download(name, content, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name;
  document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
for (const [id, name, field, type] of [
  ['download-evidence', 'evidence.json', 'evidence', 'application/json'],
  ['download-input', 'input.txt', 'inputBytes', 'text/plain'],
  ['download-output', 'output.json', 'outputBytes', 'application/json'],
]) byId(id).addEventListener('click', () => {
  if (currentMode !== 'demo' || currentDemo?.task.status !== 'Proven') return;
  download(name, field === 'evidence' ? JSON.stringify(currentDemo.evidence, null, 2) + '\n' : currentDemo[field], type);
});
byId('save-summary').addEventListener('click', () => {
  if (!loadedTask) return;
  const summary = makeSummary(loadedTask, { simulation: currentMode === 'demo', verification: evidenceVerification, scenario: currentMode === 'demo' ? currentDemo?.key : null });
  summary.observation = { observedAt: currentMode === 'live' ? observedAt : null, creationTransaction: creationObservation?.transactionHash ?? null,
    creationReceiptChecked: Boolean(creationObservation), creationBlock: creationObservation?.blockNumber ?? null };
  download('summary.json', JSON.stringify(summary, null, 2) + '\n');
});
function guideButtons() {
  byId('guide-prev').disabled = guideBusy || guideIndex <= 0;
  byId('guide-next').disabled = guideBusy;
  byId('guide-reset').disabled = guideBusy;
  byId('guide-next').textContent = guideBusy ? '正在核对…' : guideIndex < 0 ? '开始本地演示' : guideIndex === DEMO_STEPS.length - 1 ? '重试可选 Testnet 查询' : guideIndex === DEMO_STEPS.length - 2 ? '可选：读取真实 Testnet' : '下一步';
}
function cancelGuide(reset = false) {
  const wasBusy = guideBusy;
  guideGeneration++; guideBusy = false;
  if (reset && wasBusy && currentMode === 'live') {
    lookupSequence++; liveAbort?.abort(); clearTask();
    message('showcase-message', '已取消等待中的只读查询；未取得新的 Testnet 事实。');
  }
  // Manual file verification pauses the presentation but must retain the
  // next step. Only the explicit Exit action resets the journey.
  if (reset) {
    guideIndex = -1;
    byId('guide-progress').value = 0;
    text('guide-counter', '完整演示 · 约 3 分钟');
    text('guide-source', currentMode === 'live' ? 'TESTNET · READ ONLY' : 'LOCAL SIMULATION · NO WALLET');
  }
  document.body.classList.remove('guided-demo-active');
  byId('guide-exit').hidden = true;
  text('guide-result', reset ? '已退出导览；当前为手动查看。' : '当前为手动查看。可以使用下一步继续导览。');
  guideButtons();
}
async function runGuide(index) {
  if (guideBusy) return;
  document.body.classList.add('guided-demo-active');
  byId('guide-exit').hidden = false;
  guideBusy = true; const generation = ++guideGeneration;
  guideIndex = Math.min(Math.max(index, 0), DEMO_STEPS.length - 1);
  const step = DEMO_STEPS[guideIndex];
  text('guide-counter', `完整演示 · ${guideIndex + 1} / ${DEMO_STEPS.length}`);
  text('guide-source', step.action === 'live' ? 'OPTIONAL TESTNET · READ ONLY' : 'LOCAL SIMULATION · NO WALLET');
  byId('guide-progress').value = guideIndex + 1;
  text('guide-title', step.title); text('guide-description', step.text); text('guide-result', '正在准备此步骤…'); guideButtons();
  try {
    let success;
    if (step.action === 'live') success = await loadShowcase();
    else {
      await switchMode('demo');
      success = await showScenario(['verify', 'tamper'].includes(step.action) ? 'proven' : step.action);
      if (success && ['verify', 'tamper'].includes(step.action)) success = await runSampleProof(step.action === 'tamper');
    }
    if (generation !== guideGeneration) return;
    text('guide-result', step.action === 'live'
      ? (success ? '真实创建交易与任务已重新核对。完整导览完成；支付仍未独立验证。'
        : '本地六步演示已完成（模拟）。可选 Testnet 只读查询未成功；不回退为链上成功，也不重新付款。')
      : !success ? '此步骤尚未核对成功，请查看提示后重试。'
      : step.action === 'lost-ack' ? '本地六步演示已完成（模拟）：Lost ACK → PaymentUnknown → NO RECHARGE。下一步真实 Testnet 查询为可选项。'
      : step.action === 'verify' ? '原始输入、输出和 Evidence 全部匹配；并不证明支付成功。'
      : step.action === 'tamper' ? '篡改后的输出已被拒绝。支付结论保持不变。'
      : step.action === 'proven' ? '本地工具输出已生成，可以下载原始文件并独立复核。'
      : '此步骤已加载；数据来源与当前事实见下方。');
    if (step.action !== 'live') byId('studio-title').scrollIntoView({ behavior: 'auto', block: 'start' });
  } catch (error) { if (generation === guideGeneration) text('guide-result', '演示未完成：' + error.message); }
  finally { if (generation === guideGeneration) { guideBusy = false; guideButtons(); } }
}
byId('guide-next').addEventListener('click', () => void runGuide(guideIndex + 1));
byId('guide-prev').addEventListener('click', () => void runGuide(guideIndex - 1));
byId('guide-reset').addEventListener('click', () => void runGuide(0));
byId('guide-exit').addEventListener('click', () => cancelGuide(true));
byId('show-demo').addEventListener('click', () => void runGuide(0));
byId('show-live').addEventListener('click', () => { cancelGuide(); switchMode('live'); byId('live-controls').scrollIntoView({ behavior: 'auto', block: 'center' }); byId('contract').focus({ preventScroll: true }); });
for (const mode of ['demo', 'live']) byId('mode-' + mode).addEventListener('click', () => { cancelGuide(); switchMode(mode); });
for (const button of document.querySelectorAll('[data-scenario]')) button.addEventListener('click', () => { cancelGuide(); void showScenario(button.dataset.scenario); });
byId('load-showcase').addEventListener('click', () => { cancelGuide(); void loadShowcase(); });
byId('run-sample-proof').addEventListener('click', () => { cancelGuide(); void runSampleProof(); });
byId('tamper-proof').addEventListener('click', () => { cancelGuide(); void runSampleProof(true); });
byId('lookup-form').addEventListener('submit', event => { event.preventDefault(); cancelGuide(); void queryTask(byId('contract').value.trim(), byId('taskId').value.trim()); });
for (const id of ['contract', 'taskId']) byId(id).addEventListener('input', () => { lookupSequence++; liveAbort?.abort(); byId('query-button').disabled = false; if (currentMode === 'live') clearTask(); });
for (const anchor of document.querySelectorAll('.nav-item[href^="#"]')) anchor.addEventListener('click', () => {
  for (const item of document.querySelectorAll('.nav-item')) item.classList.toggle('active', item === anchor);
});
const handoff = new URLSearchParams(location.hash.slice(1));
if (handoff.get('mode') === 'live') {
  switchMode('live');
  const contract = handoff.get('contract') || '', taskId = handoff.get('taskId') || '';
  if (HEX40.test(contract) && HEX64.test(taskId)) { byId('contract').value = contract; byId('taskId').value = taskId; void queryTask(contract, taskId); }
  else message('query-message', 'Invalid task link. Enter a Ledger address and bytes32 task ID.', 'error');
} else void showScenario('created');
