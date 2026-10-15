// SPDX-License-Identifier: Apache-2.0
// Payment INTENT verification, deliberately separate from x402 settlement.
// A valid signature is approval evidence, not on-chain payment or fiat authorization.
import { verifyTypedData } from "viem";

export const APPROVAL_TYPES = {
  TaskPaymentApproval: [
    { name: "taskId", type: "bytes32" },
    { name: "descriptionHash", type: "bytes32" },
    { name: "token", type: "address" },
    { name: "payTo", type: "address" },
    { name: "amountAtomic", type: "uint256" },
    { name: "expiresAt", type: "uint64" },
    { name: "nonce", type: "bytes32" },
  ]
};
const HEX32 = /^0x[0-9a-fA-F]{64}$/;
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const SIGNATURE = /^0x[0-9a-fA-F]{130}$/;
const TOKEN = "0x534b2f3A21130d7a60830c2Df862319e593943A3";
const fail = reason => { throw new Error(reason); };
const check = (ok, why) => { if (!ok) fail(why); };
const addr = (value, field) => {
  check(typeof value === "string" && ADDRESS.test(value), "invalid " + field);
  return value.toLowerCase();
};
const hex32 = (value, field) => {
  check(typeof value === "string" && HEX32.test(value), "invalid " + field);
  return value.toLowerCase();
};
const uintString = (value, field) => {
  check(typeof value === "string" && /^(0|[1-9][0-9]*)$/.test(value), "invalid " + field);
  return BigInt(value);
};
export function buildApprovalData(quote) {
  check(quote && typeof quote === "object" && !Array.isArray(quote), "invalid approval");
  check(quote.schemaVersion === "agentledger.task-payment.v1", "invalid approval schema");
  check(quote.chainId === 10143, "wrong approval network");
  const ledger = addr(quote.ledger, "ledger");
  const taskId = hex32(quote.taskId, "taskId");
  const descriptionHash = hex32(quote.descriptionHash, "descriptionHash");
  const token = addr(quote.token, "token");
  check(token === TOKEN.toLowerCase(), "wrong USDC token");
  const payTo = addr(quote.payTo, "payTo");
  const payer = addr(quote.payer, "payer");
  const nonce = hex32(quote.nonce, "nonce");
  const amountAtomic = uintString(quote.amountAtomic, "amountAtomic");
  const expiresAt = uintString(quote.expiresAt, "expiresAt");
  check(amountAtomic > 0n && amountAtomic <= (2n ** 256n - 1n), "amount out of range");
  check(expiresAt > 0n && expiresAt <= (2n ** 64n - 1n), "expiry out of range");
  return {
    address: payer,
    domain: { name: "AgentLedger", version: "1", chainId: 10143, verifyingContract: ledger },
    primaryType: "TaskPaymentApproval",
    types: APPROVAL_TYPES,
    message: { taskId, descriptionHash, token, payTo, amountAtomic, expiresAt, nonce }
  };
}
export async function verifyTaskPaymentApproval(approval, task, opts = {}) {
  check(approval && typeof approval === "object", "approval missing");
  const { signature, ...quote } = approval;
  check(typeof signature === "string" && SIGNATURE.test(signature), "invalid EOA signature");
  const typed = buildApprovalData(quote);
  const now = opts.nowSeconds === undefined ? BigInt(Math.floor(Date.now() / 1000)) : BigInt(opts.nowSeconds);
  check(typed.message.expiresAt > now, "payment approval expired");
  check(typed.message.expiresAt <= now + 86400n, "approval expiry exceeds 24-hour limit");
  check(task && task.status === "Created", "task must exist in Created state");
  check(hex32(task.taskId, "chain taskId") === typed.message.taskId, "task ID mismatch");
  check(hex32(task.descriptionHash, "chain descriptionHash") === typed.message.descriptionHash, "task description mismatch");
  if (BigInt(task.maxBudgetAtomic) !== 0n) {
    check(typed.message.amountAtomic <= BigInt(task.maxBudgetAtomic), "exceeds task budget");
  }
  if (opts.expectedPayTo) check(addr(opts.expectedPayTo, "expected payee") === typed.message.payTo, "unapproved payee");
  const valid = await verifyTypedData({
    address: typed.address, domain: typed.domain, types: typed.types,
    primaryType: typed.primaryType, message: typed.message, signature
  });
  check(valid, "payer EIP-712 signature mismatch");
  return { quote, ...typed, verified: true };
}

/**
 * Produces a human-reviewable RECORD PAYMENT proposal; does not sign, broadcast,
 * reserve wallet budgets, or guarantee exactly-once charges.
 * Caller must derive `verifiedTransfer` directly from read-only verifyLive().
 */
export function bindApprovedSettlement(approval, verifiedTransfer, task, expectedPayTo) {
  const typed = buildApprovalData(approval);
  check(task.status === "Created", "task must be Created before recordPayment");
  check(verifiedTransfer && verifiedTransfer.network === "eip155:10143", "incorrect settlement network");
  check(hex32(verifiedTransfer.taskId, "settlement taskId") === typed.message.taskId, "settlement task mismatch");
  check(addr(verifiedTransfer.token, "settlement token") === typed.message.token, "settlement token mismatch");
  check(addr(verifiedTransfer.payer, "settlement payer") === typed.address, "settlement payer mismatch");
  check(addr(verifiedTransfer.payTo, "settlement payee") === typed.message.payTo, "settlement payee mismatch");
  check(addr(expectedPayTo, "operator expected payee") === typed.message.payTo, "operator payee mismatch");
  check(uintString(verifiedTransfer.amountAtomic, "settlement amount") === typed.message.amountAtomic, "settlement amount mismatch");
  check(hex32(verifiedTransfer.settlementRef, "settlement ref") !== "0x" + "0".repeat(64), "empty settlement ref");
  return {
    method: "recordPayment(bytes32,address,uint256,bytes32)",
    contract: typed.domain.verifyingContract,
    arguments: [typed.message.taskId, typed.address, typed.message.amountAtomic.toString(), verifiedTransfer.settlementRef],
    note: "Prepared only. Recorder MUST still enforce nonce/idempotency and reorg/finality policy before broadcasting."
  };
}
