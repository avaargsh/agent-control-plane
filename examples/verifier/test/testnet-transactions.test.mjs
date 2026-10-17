import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { encodeFunctionData, encodeFunctionResult, toFunctionSelector } from 'viem';
import { TASK_ABI } from '../lib/task-reader.mjs';
import { createTransactionJournal, prepareTask, readCreatedTask, verifyTransaction } from '../tools/testnet-transactions.mjs';

const FROM = '0x' + '12'.repeat(20), TO = '0x' + '34'.repeat(20);
const HASH = '0x' + '56'.repeat(32), BLOCK = '0x' + '78'.repeat(32);
const tx = { from: FROM, to: TO, data: '0x1234', chainId: '0x279f', value: '0x0' };
const artifact = { createTaskSelector: toFunctionSelector('createTask(bytes32,bytes32,uint256)'), getTaskSelector: toFunctionSelector('getTask(bytes32)') };
function storage() {
  const data = new Map();
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
}
function requestFor(send = async () => HASH) {
  return async method => {
    if (method === 'eth_chainId') return '0x279f';
    if (method === 'eth_accounts') return [FROM];
    if (method === 'eth_getTransactionCount') return '0x3';
    if (method === 'eth_sendTransaction') return send();
    throw Error(method);
  };
}
test('signature intent survives a lost wallet response and blocks refresh/retry', async () => {
  const local = storage(), journal = createTransactionJournal(local, 'tx');
  let sends = 0;
  await assert.rejects(() => journal.submit(requestFor(async () => {
    sends++; assert.equal(journal.load().status, 'SIGNATURE_REQUESTED'); throw Error('connection lost');
  }), tx), /connection lost/);
  const restored = createTransactionJournal(local, 'tx');
  assert.equal(restored.load().nonce, '0x3');
  await assert.rejects(() => restored.submit(requestFor(() => { sends++; return HASH; }), tx), /已有交易记录/);
  assert.equal(sends, 1);
});
test('storage failure prevents a wallet signing request', async () => {
  const local = storage(); local.setItem = () => { throw Error('storage blocked'); };
  let sends = 0;
  await assert.rejects(() => createTransactionJournal(local, 'tx').submit(requestFor(() => { sends++; }), tx), /storage blocked/);
  assert.equal(sends, 0);
});
test('explicit user cancellation allows a new deliberate request', async () => {
  const journal = createTransactionJournal(storage(), 'tx');
  await assert.rejects(() => journal.submit(requestFor(() => { throw Object.assign(Error('cancelled'), { code: 4001 }); }), tx));
  assert.equal(journal.load(), null);
  assert.equal((await journal.submit(requestFor(), tx)).transactionHash, HASH);
});
test('concurrent requests cannot open two signature prompts', async () => {
  const journal = createTransactionJournal(storage(), 'tx');
  const first = journal.submit(requestFor(), tx);
  await assert.rejects(() => journal.submit(requestFor(), tx), /正在处理|已有交易记录/);
  await first;
});
test('wallet nonce quantities normalize to canonical hexadecimal before signing and persistence', async () => {
  for (const [raw, expected] of [[0, '0x0'], [1, '0x1'], ['1', '0x1'], [1n, '0x1'], ['0x01', '0x1'], ['0X0A', '0xa']]) {
    const journal = createTransactionJournal(storage(), 'tx');
    const base = requestFor();
    let signed;
    const request = async (method, params) => {
      if (method === 'eth_getTransactionCount') return raw;
      if (method === 'eth_sendTransaction') { signed = params[0]; return HASH; }
      return base(method, params);
    };
    const saved = await journal.submit(request, tx);
    assert.equal(saved.nonce, expected);
    assert.equal(signed.nonce, expected);
  }
});
test('invalid nonce replies explain their type and cannot persist an intent or send a transaction', async () => {
  for (const raw of [null, undefined, true, '', '0x', '1e3', '-1', -1, 1.5, Number.MAX_SAFE_INTEGER + 1, {}, [], 2n ** 64n]) {
    const journal = createTransactionJournal(storage(), 'tx');
    const base = requestFor(() => assert.fail('invalid nonce must not request a signature'));
    await assert.rejects(() => journal.submit((method, params) => method === 'eth_getTransactionCount' ? raw : base(method, params), tx), /nonce.*类型/);
    assert.equal(journal.load(), null);
  }
});
test('wrong chain, changed account and corrupt stored records all block signing', async () => {
  for (const override of [{ eth_chainId: '0x1' }, { eth_accounts: [TO] }]) {
    const request = requestFor(() => assert.fail('must not sign'));
    await assert.rejects(() => createTransactionJournal(storage(), 'tx').submit((method, params) => method in override ? override[method] : request(method, params), tx));
  }
  const local = storage(); local.setItem('tx', '{bad');
  await assert.rejects(() => createTransactionJournal(local, 'tx').submit(requestFor(() => assert.fail('must not sign')), tx));
});
test('task calldata is ABI-compatible and its digest binds exact UTF-8 bytes', async () => {
  const input = '任务输入\nCount these words.\n';
  const draft = await prepareTask(artifact, input, '1000');
  const abi = [{ type: 'function', name: 'createTask', stateMutability: 'nonpayable', inputs: [
    { name: 'taskId', type: 'bytes32' }, { name: 'descriptionHash', type: 'bytes32' }, { name: 'maxBudget', type: 'uint256' },
  ], outputs: [] }];
  assert.equal(draft.descriptionHash, '0x' + createHash('sha256').update(input).digest('hex'));
  assert.equal(draft.data, encodeFunctionData({ abi, functionName: 'createTask', args: [draft.taskId, draft.descriptionHash, 1000n] }));
  assert.notEqual(draft.taskId, (await prepareTask(artifact, input, '1000')).taskId);
  const restored = await prepareTask(artifact, input, '1000', undefined, draft.taskId);
  assert.equal(restored.data, draft.data);
  await assert.rejects(() => prepareTask(artifact, input, '1000', undefined, '0x1234'), /ID 无效/);
  await assert.rejects(() => prepareTask(artifact, input, '1000', undefined, '0x' + '00'.repeat(32)), /ID 无效/);
  for (const [text, budget] of [['', '1000'], ['a', '0'], ['a', '-1'], ['a', '1e3'], ['a', (2n ** 256n).toString()]]) {
    await assert.rejects(() => prepareTask(artifact, text, budget));
  }
});
function receiptRequest(record, changes = {}) {
  return async method => {
    if (method in changes) return changes[method];
    if (method === 'eth_chainId') return '0x279f';
    if (method === 'eth_getTransactionReceipt') return { transactionHash: HASH, status: '0x1', blockNumber: '0x10', blockHash: BLOCK };
    if (method === 'eth_getTransactionByHash') return { from: record.from, to: record.to, input: record.data, nonce: '0x3', value: '0x0' };
    if (method === 'eth_getBlockByNumber') return { hash: BLOCK };
    throw Error(method);
  };
}
test('recovery checks receipt, calldata, sender, recipient, nonce, value and canonical block', async () => {
  const record = { ...tx, nonce: '0x3', transactionHash: HASH };
  assert.equal((await verifyTransaction(receiptRequest(record), record)).status, '0x1');
  assert.equal(await verifyTransaction(receiptRequest(record, { eth_getTransactionReceipt: null }), record), null);
  const good = await receiptRequest(record)('eth_getTransactionByHash');
  for (const change of [{ from: TO }, { to: FROM }, { input: '0xbeef' }, { nonce: '0x4' }, { value: '0x1' }]) {
    await assert.rejects(() => verifyTransaction(receiptRequest(record, { eth_getTransactionByHash: { ...good, ...change } }), record), /参数不一致/);
  }
  await assert.rejects(() => verifyTransaction(receiptRequest(record, { eth_getBlockByNumber: { hash: HASH } }), record), /区块发生变化/);
  const receipt = await receiptRequest(record)('eth_getTransactionReceipt');
  await assert.rejects(() => verifyTransaction(receiptRequest(record, { eth_getTransactionReceipt: { ...receipt, status: '0x0' } }), record), /回滚/);
  await assert.rejects(() => verifyTransaction(receiptRequest(record), record, '0x1234'), /完整交易哈希/);
});
test('task readback uses real ABI layout and rejects substituted task identity', async () => {
  const draft = await prepareTask(artifact, 'word count', '1000');
  const record = { ...draft, to: TO, from: FROM };
  const task = { creator: FROM, payer: '0x' + '00'.repeat(20), amount: 0n, maxBudget: 1000n, descriptionHash: draft.descriptionHash,
    evidenceHash: '0x' + '00'.repeat(32), settlementRef: '0x' + '00'.repeat(32), status: 1, createdAt: 1n, paidAt: 0n, provenAt: 0n };
  const encode = changes => encodeFunctionResult({ abi: TASK_ABI, functionName: 'getTask', result: { ...task, ...changes } });
  assert.equal((await readCreatedTask(async (method, params) => {
    assert.equal(method, 'eth_call'); assert.equal(params[0].to, TO);
    assert.equal(params[0].data, artifact.getTaskSelector + draft.taskId.slice(2)); return encode({});
  }, artifact, record)).status, 'Created');
  for (const change of [{ creator: TO }, { maxBudget: 2n }, { descriptionHash: HASH }, { status: 0 }]) {
    await assert.rejects(() => readCreatedTask(async () => encode(change), artifact, record), /创建参数不一致/);
  }
});
