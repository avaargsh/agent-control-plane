import { initLanguage } from '../public/locale.mjs';
import { createWalletConnection } from './wallet-connection.mjs';
import { createTransactionJournal, prepareTask, readTaskCreation, verifyTransaction } from './testnet-transactions.mjs';

const $ = id => document.getElementById(id);
initLanguage({ fallback: 'zh' });
const KEY = 'agentledger.monad-testnet.deployment.v1';
const address = /^0x[0-9a-fA-F]{40}$/;
let artifact;
let prepared = null;
let record = null;
let submitting = false;
let sessionVersion = 0;
let wallet;
let connecting = false;
let preparing = false;
let connectedAddress = null;
let verifiedContract = null;
let taskDraft = null;
let taskRecord = null;
let taskBusy = false;
let importedTask = null;
const deploymentJournal = createTransactionJournal(localStorage, KEY);
const taskJournal = createTransactionJournal(localStorage, KEY + '.task');
function step(current) {
  const names = ['connect', 'deploy', 'task', 'verify'];
  names.forEach((name, index) => {
    const element = $('step-' + name);
    element.classList.toggle('complete', index < names.indexOf(current));
    if (name === current) element.setAttribute('aria-current', 'step');
    else element.removeAttribute('aria-current');
  });
}
function renderDetection() {
  const detected = Boolean(wallet?.getProvider());
  $('wallet-detection').textContent = detected ? '已检测到 MetaMask 扩展。点击连接以授权当前账户。' : '此浏览器尚未检测到 MetaMask 扩展。';
  $('wallet-help').hidden = detected;
  $('connect').disabled = !detected || connecting;
  $('switch-network').hidden = !detected;
}
function walletChanged() {
  invalidate();
  connectedAddress = null;
  $('prepare').disabled = true;
  taskDraft = null; $('create-task').disabled = true;
  $('prepare-task').disabled = true;
  $('account').textContent = '钱包账户或网络已变化，请重新连接并检查。';
  renderDetection();
}
function walletRequestProgress({ method, status, message: detail }) {
  const labels = { eth_chainId: '检查钱包网络', eth_accounts: '读取已授权账户',
    eth_getTransactionCount: '读取交易 nonce', eth_sendTransaction: '请求 MetaMask 签名',
    eth_requestAccounts: '请求账户授权', eth_estimateGas: '估算 gas',
    eth_getTransactionReceipt: '读取交易回执' };
  const label = labels[method] || method;
  $('wallet-request-status').textContent = status === 'waiting'
    ? `${label}：MetaMask 尚未回应。请点击浏览器右上角的 MetaMask 图标，处理等待中的请求；请勿重复点击创建。`
    : status === 'failed' ? `${label}失败：${detail}`
    : status === 'completed' ? `${label}已完成。`
    : `正在${label}（${method}）…`;
}
wallet = createWalletConnection(window, walletChanged, { onRequest: walletRequestProgress });
const rpc = wallet.request;
wallet.discover(); renderDetection();
try {
  const response = await fetch('/artifact.json');
  if (!response.ok) throw Error('HTTP ' + response.status);
  artifact = await response.json();
} catch {
  $('status').textContent = '合约编译产物加载失败，请确认本地部署服务正在运行，然后刷新页面。';
  $('connect').disabled = true;
  throw Error('Deployment artifact unavailable');
}
function invalidate() { sessionVersion++; prepared = null; $('deploy').disabled = true; $('estimate').textContent = ''; }
async function identity() {
  if (await rpc('eth_chainId') !== '0x279f') throw Error('请在 MetaMask 切换到 Monad 测试网（Chain ID 10143），再重试。');
  const [from] = await rpc('eth_accounts');
  if (!address.test(from || '')) throw Error('请先连接 MetaMask。');
  return from;
}
function message(error) {
  $('status').textContent = error.code === 4001 ? '你已取消钱包操作，未自动重试。'
    : error.code === -32002 ? 'MetaMask 已有等待处理的请求。请点击浏览器右上角的 MetaMask 图标完成或取消该请求。'
    : error.message;
}
function showPending() {
  $('recorder').value = record.paymentRecorder;
  $('recorder').disabled = true;
  if (record.transactionHash) $('explorer').href = 'https://testnet.monadscan.com/tx/' + record.transactionHash;
  $('explorer').hidden = !record.transactionHash; $('check').hidden = false;
  $('deployment-recovery').hidden = Boolean(record.transactionHash);
  $('download').hidden = false;
  $('deploy').disabled = true;
  step('deploy');
}
async function check() {
  await identity();
  if (!record) throw Error('没有可检查的部署记录。');
  const hash = record.transactionHash || $('deployment-hash').value.trim();
  const receipt = await verifyTransaction(rpc, record, hash);
  if (!receipt) { $('status').textContent = '交易尚未确认，请稍后检查；请勿重复部署。'; return; }
  if (!address.test(receipt.contractAddress || '')) throw Error('回执中没有合约创建地址。');
  const code = await rpc('eth_getCode', [receipt.contractAddress, 'latest']);
  if (!code || code === '0x') throw Error('部署地址上没有合约代码。');
  let expectedCode = artifact.deployedBytecode.slice(2);
  for (const ref of Object.values(artifact.immutableReferences).flat()) {
    const value = record.paymentRecorder.slice(2).toLowerCase().padStart(ref.length * 2, '0');
    expectedCode = expectedCode.slice(0, ref.start * 2) + value + expectedCode.slice((ref.start + ref.length) * 2);
  }
  if (code.toLowerCase() !== '0x' + expectedCode.toLowerCase()) throw Error('链上字节码与本次编译的合约不一致。');
  const getter = artifact.abi.find(x => x.name === 'paymentRecorder');
  if (!getter) throw Error('编译产物缺少 recorder getter。');
  // paymentRecorder() selector is supplied by the compiled helper artifact.
  const recorder = await rpc('eth_call', [{ to: receipt.contractAddress, data: artifact.recorderSelector }, 'latest']);
  if (recorder.slice(-40).toLowerCase() !== record.paymentRecorder.slice(2).toLowerCase()) throw Error('链上 paymentRecorder 与部署参数不符。');
  record = deploymentJournal.save({ ...record, transactionHash: hash, contractAddress: receipt.contractAddress, blockNumber: receipt.blockNumber, blockHash: receipt.blockHash, runtimeCode: code, status: 'DEPLOYED_READBACK_VERIFIED_NOT_PAYMENT_ACCEPTANCE' });
  verifiedContract = record.contractAddress;
  showPending(); step('task');
  $('status').textContent = '部署成功，已读取链上代码和 paymentRecorder。';
  $('result').textContent = 'Ledger 合约地址：' + record.contractAddress;
  $('download').hidden = false;
  $('prepare-task').disabled = Boolean(taskRecord);
  $('task-status').textContent = taskRecord ? '已恢复任务交易，请检查原交易。' : '部署已核对，可以准备并创建任务。';
  if (importedTask && !taskRecord) await $('prepare-task').onclick();
  else if (importedTask && taskRecord?.transactionHash) await $('check-task').onclick();
}
$('detect').onclick = () => { wallet.discover(); renderDetection(); };
$('switch-network').onclick = async () => { try {
  invalidate(); $('prepare').disabled = true;
  await wallet.switchToTestnet();
  $('status').textContent = '已切换到 Monad 测试网。请连接钱包，再检查费用。';
} catch (error) { message(error); } };
$('connect').onclick = async () => { if (connecting) return; try {
  connecting = true; connectedAddress = null; renderDetection(); invalidate(); $('prepare').disabled = true;
  $('account').textContent = '正在等待 MetaMask 授权账户…';
  const accounts = await rpc('eth_requestAccounts');
  const from = Array.isArray(accounts) ? accounts[0] : null;
  if (!address.test(from || '')) {
    $('account').textContent = 'MetaMask 未返回可用账户。';
    throw Error('MetaMask 未返回可用账户。请解锁钱包，并在此网站的连接权限中勾选要使用的账户。');
  }
  // Account authorization is independent of the selected network.
  // Display the actual authorization response before any further RPC request.
  $('account').textContent = '已授权钱包地址：' + from;
  $('recorder').value = record?.paymentRecorder || from;
  $('network-status').textContent = '正在读取钱包网络…';
  const version = sessionVersion;
  const chainId = await rpc('eth_chainId');
  if (version !== sessionVersion) throw Error('钱包账户或网络在连接过程中发生变化，请重新连接。');
  $('network-status').textContent = chainId === '0x279f'
    ? '当前网络：Monad 测试网（10143）'
    : '钱包已连接，但当前网络不是 Monad 测试网（10143）。请点击上方切换网络。';
  if (chainId !== '0x279f') throw Error('已读取钱包地址。部署前请点击「切换 / 添加 Monad 测试网」（10143）。');
  connectedAddress = from;
  $('prepare').disabled = Boolean(record);
  $('prepare-task').disabled = !verifiedContract || Boolean(taskRecord);
  if (!record) step('deploy');
  $('status').textContent = '已连接。请检查 recorder 地址，再检查部署费用。';
  if (importedTask && record) await check();
} catch (error) { message(error); } finally { connecting = false; renderDetection(); } };
$('recorder').oninput = invalidate;
$('prepare').onclick = async () => { if (preparing || submitting) return; try {
  preparing = true; $('prepare').disabled = true;
  invalidate(); if (record) throw Error('已有部署记录，请检查该交易，避免重复部署。');
  const version = sessionVersion;
  const from = await identity(); const recorder = $('recorder').value.trim();
  if (!address.test(recorder) || /^0x0{40}$/i.test(recorder)) throw Error('请输入非零的 20 字节 recorder 地址。');
  const data = artifact.bytecode + recorder.slice(2).toLowerCase().padStart(64, '0');
  const tx = { from, data, value: '0x0' };
  const [gas, gasPrice, balance] = await Promise.all([rpc('eth_estimateGas', [tx]), rpc('eth_gasPrice'), rpc('eth_getBalance', [from, 'latest'])]);
  if (version !== sessionVersion) throw Error('钱包或 recorder 已变化，请重新连接并检查费用。');
  const gasLimit = (BigInt(gas) * 120n + 99n) / 100n;
  const fee = gasLimit * BigInt(gasPrice);
  $('estimate').textContent = `余额 ${(Number(BigInt(balance)) / 1e18).toFixed(6)} MON；估算费用（含 20% gas 余量） ${(Number(fee) / 1e18).toFixed(6)} MON。最终费用以 MetaMask 为准。`;
  if (BigInt(balance) < fee) throw Error('测试 MON 余额不足，请先领取测试币。');
  prepared = { ...tx, gas: '0x' + gasLimit.toString(16), gasPrice, chainId: '0x279f', paymentRecorder: recorder };
  $('deploy').disabled = false; $('status').textContent = '部署参数已准备好，点击部署后由你在 MetaMask 中确认。';
} catch (error) { message(error); } finally { preparing = false; $('prepare').disabled = !connectedAddress || Boolean(record); } };
$('deploy').onclick = async () => { if (submitting) return; try {
  if (!prepared || record) throw Error('请先检查部署参数和费用。');
  submitting = true; $('deploy').disabled = true;
  const deployment = prepared;
  const from = await identity();
  if (prepared !== deployment || from.toLowerCase() !== deployment.from.toLowerCase()) throw Error('账户或部署参数已变更，请重新检查。');
  const { paymentRecorder, ...tx } = deployment;
  record = await deploymentJournal.submit(rpc, tx, { paymentRecorder, sourceSha256: artifact.sourceSha256, compiler: artifact.compiler });
  showPending();
  $('status').textContent = '交易已提交。点击检查部署以读取回执。';
} catch (error) {
  message(error); invalidate();
  try { record = deploymentJournal.load(); if (record) { showPending(); $('status').textContent += ' 已保留提交意图；请检查原交易，不要重复部署。'; } } catch { $('status').textContent += ' 本地交易记录不可读，请核对钱包活动。'; }
} finally { submitting = false; } };
$('check').onclick = () => check().catch(message);
$('download').onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = 'agentledger-testnet-deployment.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
try { record = deploymentJournal.load();
  if (record) { showPending(); $('status').textContent = '已恢复部署记录，请连接钱包并检查；不会重复发送。'; }
} catch { $('status').textContent = '无法读取历史部署记录，请先在钱包活动中确认有无已提交部署。'; $('prepare').disabled = true; }

function taskMessage(error) {
  $('task-status').textContent = error.code === 4001 ? '你已取消创建任务，未自动重试。'
    : error.code === -32002 ? 'MetaMask 已有等待处理的请求。请点击浏览器右上角的 MetaMask 图标，完成或取消原请求；不要重复发送。'
    : error.message;
}
function invalidateTask() { taskDraft = null; $('create-task').disabled = true; $('task-estimate').textContent = ''; }
for (const id of ['task-input', 'task-budget']) $(id).addEventListener('input', () => { importedTask = null; invalidateTask(); });
function showTaskPending() {
  $('task-input').value = taskRecord.input;
  $('task-budget').value = taskRecord.maxBudgetAtomic;
  $('task-input').disabled = true; $('task-budget').disabled = true;
  $('check-task').hidden = false;
  $('task-recovery').hidden = Boolean(taskRecord.transactionHash);
  $('prepare-task').disabled = true; $('create-task').disabled = true;
  $('download-task').hidden = false;
  $('task-transaction-details').hidden = false;
  $('task-created-hash').value = taskRecord.transactionHash || '';
  $('task-transaction-state').textContent = taskRecord.transactionHash
    ? (taskRecord.status === 'TASK_READBACK_VERIFIED' ? '创建交易和链上任务已核对。' : '钱包已返回创建交易哈希，链上结果尚待检查。')
    : '已保存签名请求，但钱包尚未返回交易哈希。请先完成或取消 MetaMask 中的原请求；如果已经发送，请填写下方恢复哈希。';
  $('task-explorer').hidden = !taskRecord.transactionHash;
  if (taskRecord.transactionHash) $('task-explorer').href = 'https://testnet.monadscan.com/tx/' + taskRecord.transactionHash;
}
$('prepare-task').onclick = async () => { if (taskBusy) return; taskBusy = true; try {
  invalidateTask();
  if (!verifiedContract || taskJournal.load()) throw Error('请先核对部署；已有任务交易时请检查原交易。');
  const version = sessionVersion;
  const from = await identity();
  const input = $('task-input').value, budget = $('task-budget').value.trim();
  if (importedTask && (importedTask.from.toLowerCase() !== from.toLowerCase() || importedTask.contract.toLowerCase() !== verifiedContract.toLowerCase())) throw Error('预备任务绑定的钱包或合约不匹配。');
  const draft = await prepareTask(artifact, input, budget, undefined, importedTask?.taskId ?? null);
  if (importedTask && (draft.descriptionHash !== importedTask.descriptionHash || draft.data !== importedTask.data)) throw Error('预备任务的输入哈希或 calldata 不匹配。');
  const tx = { from, to: verifiedContract, data: draft.data, value: '0x0', chainId: '0x279f' };
  const [gas, gasPrice, balance] = await Promise.all([rpc('eth_estimateGas', [tx]), rpc('eth_gasPrice'), rpc('eth_getBalance', [from, 'latest'])]);
  if (version !== sessionVersion || input !== $('task-input').value || budget !== $('task-budget').value.trim()) throw Error('账户或任务输入已变化，请重新准备。');
  const gasLimit = (BigInt(gas) * 120n + 99n) / 100n;
  const fee = gasLimit * BigInt(gasPrice);
  if (BigInt(balance) < fee) throw Error('测试 MON 不足以支付任务创建费用。');
  taskDraft = { ...draft, tx: { ...tx, gas: '0x' + gasLimit.toString(16), gasPrice } };
  $('task-estimate').textContent = `Task ID: ${draft.taskId}；预计创建费用（含 gas 余量）：${(Number(fee) / 1e18).toFixed(6)} MON。`;
  $('create-task').disabled = false;
  $('task-status').textContent = '任务参数已准备好，输入文本和预算均已绑定。请在钱包中确认创建。';
} catch (error) { taskMessage(error); } finally { taskBusy = false; } };
$('create-task').onclick = async () => { if (taskBusy) return; taskBusy = true; let submitted = false; try {
  $('task-status').textContent = '已收到创建操作，正在核对钱包账户和网络…';
  if (!taskDraft) throw Error('请先生成任务并检查费用。');
  $('create-task').disabled = true;
  const draft = taskDraft;
  const from = await identity();
  if (taskDraft !== draft || from.toLowerCase() !== draft.tx.from.toLowerCase()) throw Error('账户或任务参数已变化，请重新准备。');
  const { tx, ...metadata } = draft;
  $('task-status').textContent = '正在等待 MetaMask 确认创建任务。请在钱包弹窗中审核并确认；此时尚不能认定交易已发送。';
  taskRecord = await taskJournal.submit(rpc, tx, metadata);
  showTaskPending();
  $('task-status').textContent = '创建交易已提交。请检查交易，确认链上任务。';
  submitted = true;
} catch (error) {
  taskMessage(error); invalidateTask();
  try { taskRecord = taskJournal.load(); if (taskRecord) { showTaskPending(); $('task-status').textContent += ' 已保存提交意图，请检查原交易。'; } } catch { $('task-status').textContent += ' 无法读取记录，请核对钱包活动。'; }
} finally { taskBusy = false; }
  if (submitted) void checkTask({ autoOpen: true });
};
async function checkTask({ autoOpen = false } = {}) { if (taskBusy) return; taskBusy = true; try {
  if (!taskRecord) throw Error('没有任务创建记录。');
  const hash = taskRecord.transactionHash || $('task-transaction-hash').value.trim();
  $('step-four').hidden = false;
  $('step-four-status').textContent = '通过本地只读服务查询测试网，核对回执、事件与任务参数…';
  $('task-status').textContent = '第 4 步：读取链上状态，无需 MetaMask 签名。';
  let observation;
  const attempts = autoOpen ? 6 : 1;
  for (let attempt = 0; attempt < attempts; attempt++) {
    observation = await readTaskCreation({ ...taskRecord, transactionHash: hash });
    if (observation.state !== 'PENDING') break;
    $('step-four-status').textContent = `创建交易仍在等待回执，已检查 ${attempt + 1} 次；只会重查，不会重发。`;
    if (attempt + 1 < attempts) await new Promise(resolve => setTimeout(resolve, 1500));
  }
  if (observation.state === 'PENDING') { $('task-status').textContent = '任务交易尚未确认，请稍后检查；不要重复发送。'; return; }
  const task = observation.task;
  taskRecord = taskJournal.save({ ...taskRecord, transactionHash: hash, blockNumber: observation.blockNumber,
    blockHash: observation.blockHash, chainStatus: task.status, status: 'TASK_READBACK_VERIFIED' });
  showTaskPending(); step('verify');
  $('task-result').textContent = `合约：${taskRecord.to}\nTask ID：${taskRecord.taskId}`;
  $('task-status').textContent = `链上任务已读取，状态：${task.status}。PaymentUnknown；尚未验证 USDC 支付。`;
  const url = new URL('/studio', window.location?.href || 'http://127.0.0.1:4322/');
  url.hash = new URLSearchParams({ mode: 'live', contract: taskRecord.to, taskId: taskRecord.taskId }).toString();
  $('open-studio').href = url.href; $('open-studio').hidden = false;
  $('step-four-status').textContent = `已核对真实链上状态：${task.status}；区块 ${observation.blockNumber}。支付仍为 PaymentUnknown。`;
  $('step-four-link').href = url.href; $('step-four-link').hidden = false;
  if (autoOpen) window.location.assign(url.href);
} catch (error) {
  taskMessage(error); $('step-four-status').textContent = '尚未完成链上核对：' + error.message;
} finally { taskBusy = false; } }
$('check-task').onclick = () => checkTask();
$('download-task').onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(taskRecord, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = 'agentledger-testnet-task.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
try { taskRecord = taskJournal.load(); if (taskRecord) { showTaskPending(); $('task-status').textContent = '已恢复任务创建记录。连接钱包后检查原交易，不会重复发送。'; } }
catch { $('task-status').textContent = '任务记录不可读，请先检查钱包活动。'; $('prepare-task').disabled = true; }

// Explicit handoff from the developer-prepared, unsigned task artifact.
if (new URLSearchParams(window.location?.hash?.slice(1) || '').get('task') === 'prepared') {
  try {
    const response = await fetch('/prepared-task.json');
    if (!response.ok) throw Error('预备任务文件不可用。');
    const request = await response.json();
    if (request.schemaVersion !== 'agentledger.prepared-testnet-task.v1' || request.chainId !== 10143
      || !address.test(request.from || '') || !address.test(request.contract || '')
      || request.deployment?.from?.toLowerCase() !== request.from.toLowerCase()
      || request.deployment?.chainId !== 10143) throw Error('预备任务格式不正确。');
    if (record && record.transactionHash !== request.deployment.transactionHash) throw Error('当前浏览器记录了另一笔部署，请使用原部署页面，避免覆盖记录。');
    const validated = await prepareTask(artifact, request.input, request.maxBudgetAtomic, undefined, request.taskId);
    if (validated.descriptionHash !== request.descriptionHash || validated.data !== request.data) throw Error('预备任务的输入与 calldata 不一致。');
    if (taskRecord && (taskRecord.taskId !== request.taskId || taskRecord.data !== request.data
      || taskRecord.to?.toLowerCase() !== request.contract.toLowerCase()
      || taskRecord.from?.toLowerCase() !== request.from.toLowerCase())) throw Error('当前浏览器保存了另一条任务，请检查原交易；不会覆盖或重复创建。');
    if (!record) record = deploymentJournal.save(request.deployment);
    showPending(); importedTask = request;
    if (taskRecord) {
      showTaskPending();
      $('task-status').textContent = '已恢复此测试任务的原交易记录。连接钱包后会核对部署，并检查已有交易；不会重复发送。';
    } else {
      $('task-input').value = request.input; $('task-budget').value = request.maxBudgetAtomic;
      $('task-status').textContent = '预备任务已加载。请连接指定钱包；页面会检查部署并准备创建参数。尚未签名或广播。';
    }
    $('task-estimate').textContent = '已准备 Task ID：' + request.taskId;
    const handoff = new URLSearchParams(window.location?.hash?.slice(1) || '');
    if (handoff.get('verify') === '1') {
      const hash = handoff.get('transactionHash');
      if (!/^0x[0-9a-fA-F]{64}$/.test(hash || '')) throw Error('缺少有效的创建交易哈希。');
      if (taskRecord?.transactionHash && taskRecord.transactionHash.toLowerCase() !== hash.toLowerCase()) throw Error('链接与原任务交易哈希不一致，不会覆盖原记录。');
      if (!taskRecord) taskRecord = taskJournal.save({ ...validated, chainId: 10143, from: request.from,
        to: request.contract, transactionHash: hash, value: '0x0', status: 'SUBMITTED' });
      showTaskPending();
      await checkTask({ autoOpen: true });
    }
  } catch (error) { taskMessage(error); }
}
