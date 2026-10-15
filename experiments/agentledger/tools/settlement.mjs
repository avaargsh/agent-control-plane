#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Non-custodial, read-only verification: this proves one USDC transfer,
// NOT the user's task authorization, execution correctness, or payment finality.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

export const NETWORK = 'eip155:10143';
export const USDC_CONTRACT = '0x534b2f3A21130d7a60830c2Df862319e593943A3';
export const DEFAULT_RPC = 'https://testnet-rpc.monad.xyz';
export const TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef';
// EIP-3009 AuthorizationUsed(address indexed authorizer, bytes32 indexed nonce).
export const ERC3009_EVENT0 = '0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5';
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const BYTES32 = /^0x[0-9a-fA-F]{64}$/;
const HEX_QUANTITY = /^0x[0-9a-fA-F]+$/;

function requireMatch(condition, message) {
  if (!condition) throw Error(message);
}
export function normalizeAddress(value, label) {
  requireMatch(typeof value === 'string' && ADDRESS.test(value), `${label} must be a valid EVM address`);
  return value.toLowerCase();
}
export function normalizeHash(value, label) {
  requireMatch(typeof value === 'string' && BYTES32.test(value), `${label} must be a bytes32 hash`);
  return value.toLowerCase();
}
function nonnegativeInteger(value, label) {
  requireMatch(typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value), `${label} must be a nonnegative decimal integer string`);
  return BigInt(value);
}
function hexQuantity(value, label) {
  requireMatch(typeof value === 'string' && HEX_QUANTITY.test(value), `${label} must be a hex quantity`);
  return BigInt(value);
}
function topicAddress(address) {
  return '0x' + '0'.repeat(24) + normalizeAddress(address, 'address').slice(2);
}
function safeLogIndex(value) {
  const index = hexQuantity(value, 'logIndex');
  requireMatch(index <= BigInt(Number.MAX_SAFE_INTEGER), 'unsafe logIndex');
  return Number(index);
}

/**
 * Check an actual Transfer event emitted by the official Monad Testnet USDC.
 * A single matching transfer is required; ambiguous receipts fail closed.
 * TaskId here is an operator-provided context, not a cryptographic binding.
 */
export function verifyReceipt(receipt, expected, currentBlock, opts = {}) {
  requireMatch(expected && typeof expected === 'object', 'expected payment required');
  const transaction = normalizeHash(expected.transaction, 'transaction');
  const payer = normalizeAddress(expected.payer, 'payer');
  const payTo = normalizeAddress(expected.payTo, 'payTo');
  const token = normalizeAddress(expected.token || USDC_CONTRACT, 'token');
  requireMatch(token === USDC_CONTRACT.toLowerCase(), 'only official Monad Testnet USDC is supported');
  const taskId = normalizeHash(expected.taskId, 'taskId');
  requireMatch(expected.network === NETWORK, 'incorrect network');
  const amount = nonnegativeInteger(expected.amountAtomic, 'amountAtomic');
  requireMatch(amount > 0n, 'zero payment forbidden');
  const minConfirmations = opts.minConfirmations ?? 2;
  requireMatch(Number.isInteger(minConfirmations) && minConfirmations >= 1 && minConfirmations <= 1000, 'invalid confirmation policy');
  requireMatch(receipt && typeof receipt === 'object', 'transaction receipt missing (pending or RPC unavailable)');
  requireMatch(receipt.status === '0x1', 'transaction reverted or status missing');
  requireMatch(normalizeHash(receipt.transactionHash, 'receipt.transactionHash') === transaction, 'transaction hash mismatch');
  normalizeHash(receipt.blockHash, 'receipt.blockHash');
  const block = hexQuantity(receipt.blockNumber, 'blockNumber');
  const head = hexQuantity(currentBlock, 'latest block');
  requireMatch(head >= block && head - block + 1n >= BigInt(minConfirmations), 'confirmation threshold not reached');
  requireMatch(Array.isArray(receipt.logs), 'receipt logs missing');
  const matches = receipt.logs.filter(log => {
    if (!log || log.removed === true) return false;
    if (typeof log.address !== 'string' || log.address.toLowerCase() !== token) return false;
    if (!Array.isArray(log.topics) || log.topics.length !== 3) return false;
    if (log.topics[0]?.toLowerCase() !== TRANSFER_TOPIC) return false;
    if (log.topics[1]?.toLowerCase() !== topicAddress(payer)) return false;
    if (log.topics[2]?.toLowerCase() !== topicAddress(payTo)) return false;
    if (typeof log.data !== 'string' || !BYTES32.test(log.data)) return false;
    return BigInt(log.data) === amount;
  });
  requireMatch(matches.length === 1, `expected exactly one matching USDC Transfer, got ${matches.length}`);
  const log = matches[0];
  requireMatch(normalizeHash(log.transactionHash, 'log.transactionHash') === transaction, 'log transaction mismatch');
  requireMatch(normalizeHash(log.blockHash, 'log.blockHash') === receipt.blockHash.toLowerCase(), 'log block mismatch');
  const logIndex = safeLogIndex(log.logIndex);
  // When a payment nonce is supplied, a same-amount unrelated Transfer is
  // insufficient. The token itself must also have consumed THAT nonce in
  // THIS receipt. This applies only to EIP-3009 settlement mode.
  let paymentNonce;
  if (Object.hasOwn(expected, 'paymentNonce')) {
    paymentNonce = normalizeHash(expected.paymentNonce, 'paymentNonce');
    const authLogs = receipt.logs.filter(entry => entry &&
      entry.removed !== true &&
      typeof entry.address === 'string' && entry.address.toLowerCase() === token &&
      Array.isArray(entry.topics) && entry.topics.length === 3 &&
      entry.topics[0]?.toLowerCase() === ERC3009_EVENT0 &&
      entry.topics[1]?.toLowerCase() === topicAddress(payer) &&
      entry.topics[2]?.toLowerCase() === paymentNonce &&
      typeof entry.transactionHash === 'string' &&
      entry.transactionHash.toLowerCase() === transaction &&
      typeof entry.blockHash === 'string' &&
      entry.blockHash.toLowerCase() === receipt.blockHash.toLowerCase()
    );
    requireMatch(authLogs.length === 1, 'expected exactly one matching USDC AuthorizationUsed event');
  }
  const material = JSON.stringify(['agentledger.settlement.v1', NETWORK, token, transaction, logIndex]);
  const settlementRef = '0x' + createHash('sha256').update(material, 'utf8').digest('hex');
  return {
    schemaVersion: 'agentledger.settlement.v1', taskId, network: NETWORK, token,
    transaction, payer, payTo, amountAtomic: amount.toString(), settlementRef,
    logIndex, blockNumber: block.toString(), confirmations: (head - block + 1n).toString(),
    ...(paymentNonce ? {paymentNonce} : {}),
    note: paymentNonce ?
      'Transfer + AuthorizationUsed nonce match. Task intent, finality and delivery still need independent evidence.' :
      'Valid transfer only: payment authorization nonce, task intent and finality were NOT established.',
  };
}

export function validatePaymentResponse(paymentResponse, expected) {
  requireMatch(paymentResponse && paymentResponse.success === true, 'x402 settlement not successful');
  requireMatch(paymentResponse.network === NETWORK, 'x402 response network mismatch');
  requireMatch(normalizeHash(paymentResponse.transaction, 'x402 transaction') === normalizeHash(expected.transaction, 'transaction'), 'x402 transaction mismatch');
  requireMatch(normalizeAddress(paymentResponse.payer, 'x402 payer') === normalizeAddress(expected.payer, 'payer'), 'x402 payer mismatch');
  if (paymentResponse.amount !== undefined) {
    requireMatch(nonnegativeInteger(paymentResponse.amount, 'x402 amount') >= nonnegativeInteger(expected.amountAtomic, 'amountAtomic'), 'settled amount lower than quote');
  }
}

export async function verifyLive(expected, rpcUrl = DEFAULT_RPC, fetchImpl = fetch, opts = {}) {
  const target = new URL(rpcUrl);
  requireMatch(target.protocol === 'https:' || target.hostname === '127.0.0.1', 'RPC must use HTTPS (or loopback for tests)');
  let nextId = 0;
  async function rpc(method, params = []) {
    const response = await fetchImpl(target, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: ++nextId, method, params }),
      signal: AbortSignal.timeout(10000),
    });
    requireMatch(response.ok, `RPC ${method} HTTP ${response.status}`);
    const data = await response.json();
    requireMatch(!data.error && data.result !== undefined, `RPC ${method} failed`);
    return data.result;
  }
  const chainId = await rpc('eth_chainId');
  requireMatch(hexQuantity(chainId, 'chainId') === 10143n, 'RPC is not Monad Testnet');
  if (expected.paymentResponse) validatePaymentResponse(expected.paymentResponse, expected);
  const receipt = await rpc('eth_getTransactionReceipt', [normalizeHash(expected.transaction, 'transaction')]);
  const currentBlock = await rpc('eth_blockNumber');
  return verifyReceipt(receipt, expected, currentBlock, opts);
}

async function main(args) {
  const [command, path] = args;
  if (command !== 'verify' || !path || args.length !== 2) {
    throw Error('usage: node tools/settlement.mjs verify EXPECTED_PAYMENT_JSON');
  }
  const expected = JSON.parse(readFileSync(path, 'utf8'));
  const result = await verifyLive(expected, process.env.MONAD_RPC_URL || DEFAULT_RPC);
  console.log(JSON.stringify(result, null, 2));
  console.error('VERIFIED TRANSFER ONLY; do not call recordPayment without independently proving task-bound payment authorization.');
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch(e => {
    console.error('SETTLEMENT NOT VERIFIED: ' + e.message);
    process.exitCode = 1;
  });
}
