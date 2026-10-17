// Read-only creation witness. This is not USDC settlement or finality acceptance.
import { decodeFunctionData, parseAbi, parseEventLogs } from 'viem';
import { readTask, validateLookup } from './task-reader.mjs';

const ABI = parseAbi([
  'function createTask(bytes32 taskId, bytes32 descriptionHash)',
  'function createTask(bytes32 taskId, bytes32 descriptionHash, uint256 maxBudget)',
  'event TaskCreated(bytes32 indexed taskId, address indexed creator, bytes32 descriptionHash, uint256 maxBudget)',
]);
const HASH = /^0x[0-9a-fA-F]{64}$/;
const same = (a, b) => typeof a === 'string' && typeof b === 'string' && a.toLowerCase() === b.toLowerCase();
function check(condition, code) {
  if (!condition) throw Object.assign(Error(code), { code });
}

export async function observeTaskCreation(client, contract, taskId, transactionHash) {
  validateLookup(contract, taskId);
  check(HASH.test(transactionHash || ''), 'INVALID_TRANSACTION_HASH');
  check(await client.getChainId() === 10143, 'WRONG_CHAIN');
  let receipt;
  try { receipt = await client.getTransactionReceipt({ hash: transactionHash }); }
  catch (error) {
    if (error.name !== 'TransactionReceiptNotFoundError') throw error;
  }
  if (!receipt) return { state: 'PENDING', chainId: 10143, contract, taskId, transactionHash };
  check(receipt.status === 'success', 'CREATION_REVERTED');
  check(same(receipt.transactionHash, transactionHash) && HASH.test(receipt.blockHash || ''), 'RECEIPT_MISMATCH');
  const tx = await client.getTransaction({ hash: transactionHash });
  check(tx && same(tx.hash, transactionHash) && same(tx.to, contract) && tx.value === 0n, 'TRANSACTION_MISMATCH');
  let call;
  try { call = decodeFunctionData({ abi: ABI, data: tx.input }); }
  catch { check(false, 'NOT_TASK_CREATION'); }
  check(call.functionName === 'createTask' && same(call.args[0], taskId), 'TASK_ID_MISMATCH');
  const [id, descriptionHash, budget = 0n] = call.args;
  const events = parseEventLogs({ abi: ABI, eventName: 'TaskCreated', logs: receipt.logs, strict: true })
    .filter(log => same(log.address, contract));
  check(events.length === 1 && same(events[0].args.taskId, id)
    && same(events[0].args.creator, tx.from) && same(events[0].args.descriptionHash, descriptionHash)
    && events[0].args.maxBudget === budget, 'CREATION_EVENT_MISMATCH');
  const task = await readTask(client, contract, taskId);
  check(same(task.creator, tx.from) && same(task.descriptionHash, descriptionHash)
    && task.maxBudgetAtomic === budget.toString(), 'TASK_READBACK_MISMATCH');
  const canonical = await client.getBlock({ blockNumber: receipt.blockNumber });
  check(canonical.number === receipt.blockNumber && same(canonical.hash, receipt.blockHash), 'CREATION_BLOCK_CHANGED');
  return { state: 'CONFIRMED', chainId: 10143, contract, taskId, transactionHash,
    from: tx.from, data: tx.input, nonce: '0x' + BigInt(tx.nonce).toString(16), value: '0x0',
    blockNumber: receipt.blockNumber.toString(), blockHash: receipt.blockHash, task,
    payment: 'PaymentUnknown', finalityProven: false, publicPaymentAcceptance: false };
}
