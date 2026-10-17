// SPDX-License-Identifier: Apache-2.0
// EIP-712 TaskPaymentApproval encoding.
import {
  assertAddress,
  assertHex32,
  assertPositiveUintString,
  APPROVAL_TYPES,
  MONAD_CHAIN_ID,
  USDC_CONTRACT,
  type Eip712ApprovalData,
  type TaskPaymentApprovalQuote,
} from "./types.js";

function buildApprovalData(quote: unknown): Eip712ApprovalData {
  if (!quote || typeof quote !== "object" || Array.isArray(quote)) {
    throw new Error("invalid approval");
  }
  const q = quote as TaskPaymentApprovalQuote;
  if (q.schemaVersion !== "agentledger.task-payment.v1") {
    throw new Error("invalid approval schema");
  }
  if (q.chainId !== MONAD_CHAIN_ID) {
    throw new Error("wrong approval network");
  }
  const ledger = assertAddress(q.ledger, "ledger");
  const taskId = assertHex32(q.taskId, "taskId");
  const descriptionHash = assertHex32(q.descriptionHash, "descriptionHash");
  const token = assertAddress(q.token, "token");
  if (token !== USDC_CONTRACT.toLowerCase()) {
    throw new Error("wrong USDC token");
  }
  const payTo = assertAddress(q.payTo, "payTo");
  const payer = assertAddress(q.payer, "payer");
  const nonce = assertHex32(q.nonce, "nonce");
  const amountAtomic = assertPositiveUintString(q.amountAtomic, "amountAtomic");
  const expiresAt = assertPositiveUintString(q.expiresAt, "expiresAt");
  if (amountAtomic >= 2n ** 256n) throw new Error("amount out of range");
  if (expiresAt >= 2n ** 64n) throw new Error("expiry out of range");
  return {
    address: payer,
    domain: {
      name: "AgentLedger",
      version: "1",
      chainId: MONAD_CHAIN_ID,
      verifyingContract: ledger,
    },
    primaryType: "TaskPaymentApproval",
    types: APPROVAL_TYPES,
    message: { taskId, descriptionHash, token, payTo, amountAtomic, expiresAt, nonce },
  };
}

export { buildApprovalData };
