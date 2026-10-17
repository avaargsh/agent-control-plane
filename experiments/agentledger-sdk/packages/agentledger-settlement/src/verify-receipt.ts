// SPDX-License-Identifier: Apache-2.0
// Read-only USDC Transfer + EIP-3009 AuthorizationUsed receipt verification.
import { createHash } from "node:crypto";
import {
  assertAddress,
  assertHex32,
  assertPositiveUintString,
  USDC_CONTRACT,
} from "@agentledger/core";

export const NETWORK = "eip155:10143";
export const USDC = USDC_CONTRACT.toLowerCase();
export const TRANSFER_TOPIC =
  "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef";
export const ERC3009_EVENT0 =
  "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5";

const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const BYTES32 = /^0x[0-9a-fA-F]{64}$/;
const HEX_QUANTITY = /^0x[0-9a-fA-F]+$/;

export function requireMatch(condition: boolean, message: string): void {
  if (!condition) throw Error(message);
}

export function normalizeAddress(value: unknown, label: string): string {
  requireMatch(typeof value === "string" && ADDRESS.test(value),
    `${label} must be a valid EVM address`);
  return (value as string).toLowerCase();
}

export function normalizeHash(value: unknown, label: string): string {
  requireMatch(typeof value === "string" && BYTES32.test(value),
    `${label} must be a bytes32 hash`);
  return (value as string).toLowerCase();
}

function nonnegativeInteger(value: unknown, label: string): bigint {
  requireMatch(typeof value === "string" && /^(0|[1-9][0-9]*)$/.test(value),
    `${label} must be a nonnegative decimal integer string`);
  return BigInt(value as string);
}

export function hexQuantity(value: unknown, label: string): bigint {
  requireMatch(typeof value === "string" && HEX_QUANTITY.test(value),
    `${label} must be a hex quantity`);
  return BigInt(value as string);
}

function topicAddress(address: string): string {
  return "0x" + "0".repeat(24) + normalizeAddress(address, "address").slice(2);
}

export function safeLogIndex(value: unknown): number {
  const index = hexQuantity(value, "logIndex");
  requireMatch(index <= BigInt(Number.MAX_SAFE_INTEGER), "unsafe logIndex");
  return Number(index);
}

export interface VerifyReceiptExpected {
  transaction: string;
  payer: string;
  payTo: string;
  taskId: string;
  network: string;
  amountAtomic: string;
  token?: string;
  paymentNonce?: string;
}

export interface ReceiptLog {
  removed?: boolean;
  address?: string;
  topics?: Array<string>;
  data?: string;
  logIndex?: string;
  transactionHash?: string;
  blockHash?: string;
}

export interface ReceiptLike {
  status?: string;
  transactionHash?: string;
  blockHash?: string;
  blockNumber?: string;
  logs?: ReceiptLog[];
  transactionIndex?: string;
}

export interface PaymentReceipt {
  schemaVersion: string;
  taskId: string;
  network: string;
  token: string;
  transaction: string;
  payer: string;
  payTo: string;
  amountAtomic: string;
  settlementRef: string;
  logIndex: number;
  blockNumber: string;
  blockHash: string;
  confirmations: string;
  paymentNonce?: string;
  transactionIndex?: number | null;
  canonicalBlockCheckedAtObservation?: boolean;
  observedHeadBlockNumber?: string;
  note?: string;
}

export function verifyReceipt(
  receipt: ReceiptLike | null | undefined,
  expected: VerifyReceiptExpected,
  currentBlock: string,
  opts: { minConfirmations?: number } = {},
): PaymentReceipt {
  requireMatch(expected && typeof expected === "object", "expected payment required");
  const transaction = normalizeHash(expected.transaction, "transaction");
  const payer = normalizeAddress(expected.payer, "payer");
  const payTo = normalizeAddress(expected.payTo, "payTo");
  const token = normalizeAddress(expected.token ?? USDC, "token");
  requireMatch(token === USDC, "only official Monad Testnet USDC is supported");
  const taskId = normalizeHash(expected.taskId, "taskId");
  requireMatch(expected.network === NETWORK, "incorrect network");
  const amount = assertPositiveUintString(expected.amountAtomic, "amountAtomic");
  const minConfirmations = opts.minConfirmations ?? 2;
  requireMatch(Number.isInteger(minConfirmations) && minConfirmations >= 1 &&
    minConfirmations <= 1000, "invalid confirmation policy");
  if (!receipt || typeof receipt !== "object") {
    throw Error("transaction receipt missing (pending or RPC unavailable)");
  }
  requireMatch(receipt.status === "0x1", "transaction reverted or status missing");
  requireMatch(normalizeHash(receipt.transactionHash, "receipt.transactionHash") === transaction,
    "transaction hash mismatch");
  normalizeHash(receipt.blockHash, "receipt.blockHash");
  const block = hexQuantity(receipt.blockNumber, "blockNumber");
  const head = hexQuantity(currentBlock, "latest block");
  requireMatch(head >= block && head - block + 1n >= BigInt(minConfirmations),
    "confirmation threshold not reached");
  if (!Array.isArray(receipt.logs)) {
    throw Error("receipt logs missing");
  }
  const matches = receipt.logs.filter((log): boolean => {
    if (!log || log.removed === true) return false;
    if (typeof log.address !== "string" || log.address.toLowerCase() !== token) return false;
    if (!Array.isArray(log.topics) || log.topics.length !== 3) return false;
    if (log.topics[0]?.toLowerCase() !== TRANSFER_TOPIC) return false;
    if (log.topics[1]?.toLowerCase() !== topicAddress(payer)) return false;
    if (log.topics[2]?.toLowerCase() !== topicAddress(payTo)) return false;
    if (typeof log.data !== "string" || !/^0x[0-9a-fA-F]{64}$/.test(log.data)) return false;
    return BigInt(log.data) === amount;
  });
  requireMatch(matches.length === 1,
    `expected exactly one matching USDC Transfer, got ${matches.length}`);
  const log = matches[0]!;
  requireMatch(normalizeHash(log.transactionHash, "log.transactionHash") === transaction,
    "log transaction mismatch");
  requireMatch(normalizeHash(log.blockHash, "log.blockHash") ===
    (receipt.blockHash as string).toLowerCase(), "log block mismatch");
  const logIndex = safeLogIndex(log.logIndex);
  const transactionIndex = receipt.transactionIndex === undefined ? null :
    safeLogIndex(receipt.transactionIndex);
  let paymentNonce: string | undefined;
  if (Object.hasOwn(expected, "paymentNonce")) {
    paymentNonce = normalizeHash(expected.paymentNonce, "paymentNonce");
    const authLogs = receipt.logs.filter((entry): boolean => entry &&
      entry.removed !== true &&
      typeof entry.address === "string" && entry.address.toLowerCase() === token &&
      Array.isArray(entry.topics) && entry.topics.length === 3 &&
      entry.topics[0]?.toLowerCase() === ERC3009_EVENT0 &&
      entry.topics[1]?.toLowerCase() === topicAddress(payer) &&
      entry.topics[2]?.toLowerCase() === paymentNonce &&
      typeof entry.transactionHash === "string" &&
      entry.transactionHash.toLowerCase() === transaction &&
      typeof entry.blockHash === "string" &&
      entry.blockHash.toLowerCase() === (receipt.blockHash as string).toLowerCase()
    );
    requireMatch(authLogs.length === 1,
      "expected exactly one matching USDC AuthorizationUsed event");
  }
  const material = JSON.stringify(
    ["agentledger.settlement.v1", NETWORK, token, transaction, logIndex]);
  const settlementRef = "0x" + createHash("sha256").update(material, "utf8").digest("hex");
  return {
    schemaVersion: "agentledger.settlement.v1",
    taskId,
    network: NETWORK,
    token,
    transaction,
    payer,
    payTo,
    amountAtomic: amount.toString(),
    settlementRef,
    logIndex,
    transactionIndex,
    blockNumber: block.toString(),
    blockHash: (receipt.blockHash as string).toLowerCase(),
    confirmations: (head - block + 1n).toString(),
    ...(paymentNonce ? { paymentNonce } : {}),
    note: paymentNonce ?
      "Transfer + AuthorizationUsed nonce match. Task intent, finality and delivery still need independent evidence." :
      "Valid transfer only: payment authorization nonce, task intent and finality were NOT established.",
  };
}

export function validatePaymentResponse(
  paymentResponse: unknown,
  expected: VerifyReceiptExpected,
): void {
  if (!paymentResponse || typeof paymentResponse !== "object") {
    throw Error("x402 settlement response missing");
  }
  const pr = paymentResponse as { success?: unknown; network?: unknown; transaction?: unknown; payer?: unknown; amount?: unknown };
  requireMatch(pr.success === true, "x402 settlement not successful");
  requireMatch(pr.network === NETWORK, "x402 response network mismatch");
  requireMatch(normalizeHash(pr.transaction, "x402 transaction") ===
    normalizeHash(expected.transaction, "transaction"), "x402 transaction mismatch");
  requireMatch(normalizeAddress(pr.payer, "x402 payer") ===
    normalizeAddress(expected.payer, "payer"), "x402 payer mismatch");
  if (pr.amount !== undefined) {
    requireMatch(nonnegativeInteger(pr.amount, "x402 amount") >=
      assertPositiveUintString(expected.amountAtomic, "amountAtomic"),
      "settled amount lower than quote");
  }
}
