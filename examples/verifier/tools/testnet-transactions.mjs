const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const HASH = /^0x[0-9a-fA-F]{64}$/;
const HEX = /^0x(?:[0-9a-fA-F]{2})*$/;
const same = (a, b) => typeof a === 'string' && typeof b === 'string' && a.toLowerCase() === b.toLowerCase();
const requireValue = (ok, reason) => { if (!ok) throw Error(reason); };

function normalizeNonce(raw) {
  const type = raw === null ? 'null' : Array.isArray(raw) ? 'array' : typeof raw;
  const description = ['string', 'number', 'bigint', 'boolean'].includes(type)
    ? `类型：${type}，值：${String(raw).slice(0, 80)}` : `类型：${type}`;
  const invalid = () => Error(`钱包未返回有效交易 nonce（${description}）。预期非负整数或 0x 十六进制字符串；尚未请求签名。`);
  let nonce;
  if (typeof raw === 'bigint') nonce = raw;
  else if (typeof raw === 'number' && Number.isSafeInteger(raw)) nonce = BigInt(raw);
  else if (typeof raw === 'string' && (/^0[xX][0-9a-fA-F]+$/.test(raw) || /^[0-9]+$/.test(raw))) nonce = BigInt(raw);
  else throw invalid();
  if (nonce < 0n || nonce >= 2n ** 64n) throw invalid();
  return '0x' + nonce.toString(16);
}

// Persist intent BEFORE opening a signature request. Reloads cannot silently resend it.
export function createTransactionJournal(storage, key) {
  let submitting = false;
  function load() {
    const raw = storage.getItem(key);
    if (!raw) return null;
    const record = JSON.parse(raw);
    requireValue(record?.chainId === 10143 && ADDRESS.test(record.from) && HEX.test(record.data), '本地交易记录损坏，请先核对钱包活动；禁止自动重发。');
    if (record.transactionHash) requireValue(HASH.test(record.transactionHash), '本地交易哈希无效。');
    return record;
  }
  function save(record) { storage.setItem(key, JSON.stringify(record)); return record; }
  async function submit(request, tx, metadata = {}) {
    requireValue(!submitting, '已有签名请求正在处理，不会重复提交。');
    submitting = true;
    try {
    requireValue(!load(), '已有交易记录，请先检查原交易；不会重复提交。');
    requireValue(await request('eth_chainId') === '0x279f', '只允许 Monad 测试网。');
    const [account] = await request('eth_accounts');
    requireValue(same(account, tx.from), '钱包账户已变化，请重新检查。');
    const nonce = normalizeNonce(await request('eth_getTransactionCount', [tx.from, 'pending']));
    requireValue(!load(), '另一页面已保存交易记录，请检查原交易。');
    const record = save({ ...metadata, chainId: 10143, from: tx.from, to: tx.to ?? null,
      data: tx.data, value: tx.value ?? '0x0', nonce, status: 'SIGNATURE_REQUESTED' });
    try {
      const transactionHash = await request('eth_sendTransaction', [{ ...tx, nonce }]);
      requireValue(HASH.test(transactionHash), '钱包未返回有效交易哈希，请先检查钱包活动。');
      return save({ ...record, transactionHash, status: 'SUBMITTED' });
    } catch (error) {
      // Only an explicit user rejection proves that this signature request was cancelled.
      if (error.code === 4001) storage.removeItem(key);
      throw error;
    }
    } finally { submitting = false; }
  }
  return { load, save, submit };
}

export async function verifyTransaction(request, record, hash = record.transactionHash) {
  requireValue(HASH.test(hash || ''), '请粘贴钱包活动中的完整交易哈希。');
  requireValue(await request('eth_chainId') === '0x279f', '只允许 Monad 测试网。');
  const receipt = await request('eth_getTransactionReceipt', [hash]);
  if (!receipt) return null;
  requireValue(receipt.status === '0x1', '交易已回滚，请查看浏览器中的失败原因；不会自动重发。');
  requireValue(same(receipt.transactionHash, hash), '回执交易哈希不匹配。');
  const tx = await request('eth_getTransactionByHash', [hash]);
  requireValue(tx && same(tx.from, record.from) && same(tx.input, record.data)
    && (record.to ? same(tx.to, record.to) : tx.to === null)
    && BigInt(tx.value ?? '0x0') === BigInt(record.value ?? '0x0')
    && (!record.nonce || BigInt(tx.nonce) === BigInt(record.nonce)), '交易与本地准备的参数不一致。');
  requireValue(typeof receipt.blockNumber === 'string' && HASH.test(receipt.blockHash || ''), '回执缺少有效区块信息。');
  const block = await request('eth_getBlockByNumber', [receipt.blockNumber, false]);
  requireValue(block && same(block.hash, receipt.blockHash), '交易所在区块发生变化，请稍后检查。');
  return receipt;
}

export async function prepareTask(artifact, input, budget, cryptoApi = globalThis.crypto, requestedTaskId = null) {
  const bytes = new TextEncoder().encode(input);
  requireValue(bytes.length > 0 && bytes.length <= 100_000, '任务输入应为 1–100000 字节的文本。');
  requireValue(/^[1-9][0-9]*$/.test(budget), '预算上限必须是正整数（USDC atomic units）。');
  const amount = BigInt(budget);
  requireValue(amount < 2n ** 256n, '预算超出 uint256 范围。');
  const toHex = buffer => '0x' + Array.from(new Uint8Array(buffer), value => value.toString(16).padStart(2, '0')).join('');
  if (requestedTaskId !== null) requireValue(HASH.test(requestedTaskId) && !/^0x0{64}$/i.test(requestedTaskId), '预备任务 ID 无效。');
  const taskId = requestedTaskId || toHex(cryptoApi.getRandomValues(new Uint8Array(32)));
  const descriptionHash = toHex(await cryptoApi.subtle.digest('SHA-256', bytes));
  return { taskId, descriptionHash, maxBudgetAtomic: budget, input,
    data: artifact.createTaskSelector + taskId.slice(2) + descriptionHash.slice(2) + amount.toString(16).padStart(64, '0') };
}

export async function readCreatedTask(request, artifact, record) {
  const data = await request('eth_call', [{ to: record.to,
    data: artifact.getTaskSelector + record.taskId.slice(2) }, 'latest']);
  requireValue(/^0x[0-9a-fA-F]{704}$/.test(data || ''), 'getTask 返回值格式不正确。');
  const words = data.slice(2).match(/.{64}/g);
  const task = { creator: '0x' + words[0].slice(24), maxBudgetAtomic: BigInt('0x' + words[3]).toString(),
    descriptionHash: '0x' + words[4], status: Number(BigInt('0x' + words[7])) };
  requireValue(same(task.creator, record.from) && same(task.descriptionHash, record.descriptionHash)
    && task.maxBudgetAtomic === record.maxBudgetAtomic && [1, 2, 3].includes(task.status), '链上任务与创建参数不一致。');
  return { ...task, status: ['None', 'Created', 'Paid', 'Proven'][task.status] };
}

// Step four reads the fixed same-origin service without the wallet provider.
export async function readTaskCreation(record, fetchImpl = globalThis.fetch) {
  requireValue(ADDRESS.test(record.to || '') && HASH.test(record.taskId || '')
    && HASH.test(record.transactionHash || ''), '缺少有效的合约、Task ID 或创建交易哈希。');
  const params = new URLSearchParams({ contract: record.to, taskId: record.taskId, transactionHash: record.transactionHash });
  const response = await fetchImpl('/api/task-creation?' + params, { cache: 'no-store', signal: AbortSignal.timeout(15_000) });
  const result = await response.json();
  if (!response.ok) {
    const reasons = { CREATION_REVERTED: '创建交易已回滚', CREATION_BLOCK_CHANGED: '创建交易所在区块发生变化',
      RPC_UNAVAILABLE: '测试网 RPC 暂时不可用', WRONG_CHAIN: '只读服务连接的网络不正确' };
    throw Error((reasons[result.error] || '创建交易或链上任务核对失败：' + result.error) + '；不会重新发送交易。');
  }
  requireValue(result.chainId === 10143 && same(result.contract, record.to) && same(result.taskId, record.taskId)
    && same(result.transactionHash, record.transactionHash), '只读服务返回的任务身份不一致。');
  if (result.state === 'PENDING') return result;
  const task = result.task;
  requireValue(result.state === 'CONFIRMED' && same(result.from, record.from) && same(result.data, record.data)
    && (!record.nonce || BigInt(result.nonce) === BigInt(record.nonce)) && result.value === '0x0'
    && task?.chainId === 10143 && same(task.contract, record.to) && same(task.taskId, record.taskId)
    && same(task.creator, record.from) && same(task.descriptionHash, record.descriptionHash)
    && task.maxBudgetAtomic === record.maxBudgetAtomic && ['Created', 'Paid', 'Proven'].includes(task.status),
  '链上创建交易或任务参数与本地记录不一致。');
  return result;
}
