// SPDX-License-Identifier: Apache-2.0
// Pure, shared EIP-712 task-payment encoding; used by both recorder and HTTP gate.
// This file MUST remain dependency-free so both package entrypoints agree.
export const MONAD_CHAIN_ID = 10143;
export const MONAD_NETWORK = "eip155:10143";
export const USDC_CONTRACT = "0x534b2f3A21130d7a60830c2Df862319e593943A3";
export const APPROVAL_TYPES = {
  TaskPaymentApproval: [
    { name: "taskId", type: "bytes32" },
    { name: "descriptionHash", type: "bytes32" },
    { name: "token", type: "address" },
    { name: "payTo", type: "address" },
    { name: "amountAtomic", type: "uint256" },
    { name: "expiresAt", type: "uint64" },
    { name: "nonce", type: "bytes32" },
  ],
};
const HEX32 = /^0x[0-9a-fA-F]{64}$/;
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
export const assertHex32 = (value, label) => {
  if (typeof value !== "string" || !HEX32.test(value)) throw Error("invalid " + label);
  return value.toLowerCase();
};
export const assertAddress = (value, label) => {
  if (typeof value !== "string" || !ADDRESS.test(value)) throw Error("invalid " + label);
  return value.toLowerCase();
};
export const assertUintString = (value, label) => {
  if (typeof value !== "string" || !/^(0|[1-9][0-9]*)$/.test(value)) {
    throw Error("invalid " + label);
  }
  return BigInt(value);
};
export function buildApprovalData(quote) {
  if (!quote || typeof quote !== "object" || Array.isArray(quote)) throw Error("invalid approval");
  if (quote.schemaVersion !== "agentledger.task-payment.v1") throw Error("invalid approval schema");
  if (quote.chainId !== MONAD_CHAIN_ID) throw Error("wrong approval network");
  const ledger = assertAddress(quote.ledger, "ledger");
  const taskId = assertHex32(quote.taskId, "taskId");
  const descriptionHash = assertHex32(quote.descriptionHash, "descriptionHash");
  const token = assertAddress(quote.token, "token");
  if (token !== USDC_CONTRACT.toLowerCase()) throw Error("wrong USDC token");
  const payTo = assertAddress(quote.payTo, "payTo");
  const payer = assertAddress(quote.payer, "payer");
  const nonce = assertHex32(quote.nonce, "nonce");
  const amountAtomic = assertUintString(quote.amountAtomic, "amountAtomic");
  const expiresAt = assertUintString(quote.expiresAt, "expiresAt");
  if (amountAtomic <= 0n || amountAtomic >= 2n ** 256n) throw Error("amount out of range");
  if (expiresAt <= 0n || expiresAt >= 2n ** 64n) throw Error("expiry out of range");
  return {
    address: payer,
    domain: { name: "AgentLedger", version: "1", chainId: MONAD_CHAIN_ID, verifyingContract: ledger },
    primaryType: "TaskPaymentApproval",
    types: APPROVAL_TYPES,
    message: { taskId, descriptionHash, token, payTo, amountAtomic, expiresAt, nonce },
  };
}
