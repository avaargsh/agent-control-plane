import test from 'node:test';
import assert from 'node:assert/strict';
import { encodeFunctionData, encodeEventTopics, encodeAbiParameters, parseAbi } from 'viem';
import { observeTaskCreation } from '../lib/task-creation.mjs';
import { createVerifierServer } from '../server.mjs';
import { readTaskCreation } from '../tools/testnet-transactions.mjs';

const CONTRACT = '0x' + '12'.repeat(20), FROM = '0x' + '34'.repeat(20);
const TASK = '0x' + '56'.repeat(32), HASH = '0x' + '78'.repeat(32), BLOCK = '0x' + '9a'.repeat(32), DESCRIPTION = '0x' + 'bc'.repeat(32);
const abi = parseAbi(['function createTask(bytes32 taskId, bytes32 descriptionHash, uint256 maxBudget)',
  'event TaskCreated(bytes32 indexed taskId, address indexed creator, bytes32 descriptionHash, uint256 maxBudget)']);
const data = encodeFunctionData({ abi, functionName: 'createTask', args: [TASK, DESCRIPTION, 1000n] });
const log = { address: CONTRACT, topics: encodeEventTopics({ abi, eventName: 'TaskCreated', args: { taskId: TASK, creator: FROM } }),
  data: encodeAbiParameters([{ type: 'bytes32' }, { type: 'uint256' }], [DESCRIPTION, 1000n]) };
const receipt = { status: 'success', transactionHash: HASH, blockNumber: 100n, blockHash: BLOCK, logs: [log] };
const tx = { hash: HASH, from: FROM, to: CONTRACT, input: data, nonce: 1, value: 0n };
const task = { creator: FROM, payer: '0x' + '00'.repeat(20), amount: 0n, maxBudget: 1000n,
  descriptionHash: DESCRIPTION, evidenceHash: '0x' + '00'.repeat(32), settlementRef: '0x' + '00'.repeat(32),
  status: 1, createdAt: 1n, paidAt: 0n, provenAt: 0n };
function client(overrides = {}) {
  return { getChainId: async () => 10143, getTransactionReceipt: async () => receipt,
    getTransaction: async () => tx, getBytecode: async () => '0x6000', readContract: async () => task,
    getBlock: async () => ({ number: 100n, hash: BLOCK }), ...overrides };
}
const record = { from: FROM, to: CONTRACT, taskId: TASK, transactionHash: HASH, data,
  descriptionHash: DESCRIPTION, maxBudgetAtomic: '1000', nonce: '0x1' };

test('step four independently verifies creation receipt, event, canonical block and task', async () => {
  const result = await observeTaskCreation(client(), CONTRACT, TASK, HASH);
  assert.equal(result.state, 'CONFIRMED');
  assert.equal(result.task.status, 'Created');
  assert.equal(result.payment, 'PaymentUnknown');
  assert.equal(result.finalityProven, false);
  assert.equal(result.publicPaymentAcceptance, false);
  const read = await readTaskCreation(record, async url => {
    assert.match(url, /^\/api\/task-creation\?/);
    assert(!url.includes('rpc='));
    return { ok: true, json: async () => result };
  });
  assert.equal(read.task.taskId, TASK);
});
test('missing creation receipt is pending, while RPC failures remain failures', async () => {
  const pending = await observeTaskCreation(client({ getTransactionReceipt: async () => { throw Object.assign(Error('missing'), { name: 'TransactionReceiptNotFoundError' }); } }), CONTRACT, TASK, HASH);
  assert.equal(pending.state, 'PENDING');
  assert.equal((await readTaskCreation(record, async () => ({ ok: true, json: async () => pending }))).state, 'PENDING');
  await assert.rejects(() => observeTaskCreation(client({ getTransactionReceipt: async () => { throw Error('RPC down'); } }), CONTRACT, TASK, HASH), /RPC down/);
});
test('wrong chain, revert, substituted calldata/event/task and reorg never confirm step four', async () => {
  for (const overrides of [
    { getChainId: async () => 1 },
    { getTransactionReceipt: async () => ({ ...receipt, status: 'reverted' }) },
    { getTransaction: async () => ({ ...tx, to: FROM }) },
    { getTransaction: async () => ({ ...tx, value: 1n }) },
    { getTransaction: async () => ({ ...tx, input: '0x1234' }) },
    { getTransaction: async () => ({ ...tx, input: encodeFunctionData({ abi, functionName: 'createTask', args: [HASH, DESCRIPTION, 1000n] }) }) },
    { getTransactionReceipt: async () => ({ ...receipt, logs: [] }) },
    { getTransactionReceipt: async () => ({ ...receipt, logs: [{ ...log, address: FROM }] }) },
    { readContract: async () => ({ ...task, creator: CONTRACT }) },
    { readContract: async () => ({ ...task, descriptionHash: HASH }) },
    { getBlock: async () => ({ number: 100n, hash: HASH }) },
  ]) await assert.rejects(() => observeTaskCreation(client(overrides), CONTRACT, TASK, HASH));
});
test('browser rejects a confirmation for another task, creator, nonce or input', async () => {
  const result = await observeTaskCreation(client(), CONTRACT, TASK, HASH);
  for (const change of [{ taskId: HASH }, { from: CONTRACT }, { nonce: '0x2' }, { data: '0xbeef' },
    { task: { ...result.task, descriptionHash: HASH } }, { task: { ...result.task, maxBudgetAtomic: '1' } }]) {
    await assert.rejects(() => readTaskCreation(record, async () => ({ ok: true, json: async () => ({ ...result, ...change }) })));
  }
});
test('creation observation HTTP endpoint is read-only, validates identities and distinguishes pending', async () => {
  let calls = 0;
  const server = createVerifierServer({ observeCreation: async () => { calls++; return { state: 'PENDING' }; } });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = 'http://127.0.0.1:' + server.address().port;
  const query = new URLSearchParams({ contract: CONTRACT, taskId: TASK, transactionHash: HASH });
  try {
    assert.equal((await fetch(base + '/api/task-creation?' + query, { method: 'POST' })).status, 405);
    assert.equal((await fetch(base + '/api/task-creation?contract=invalid')).status, 400);
    assert.equal((await fetch(base + '/api/task-creation?' + query + '&rpc=https://attacker.example')).status, 400);
    assert.equal(calls, 0);
    const response = await fetch(base + '/api/task-creation?' + query);
    assert.equal(response.status, 202);
    assert.equal((await response.json()).state, 'PENDING');
    assert.equal(calls, 1);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
