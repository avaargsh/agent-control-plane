// SPDX-License-Identifier: Apache-2.0
// Payer EIP-712 intent verification, separate from x402 settlement observation.
import { verifyTypedData } from "viem";
import {
  buildApprovalData, APPROVAL_TYPES, assertAddress, assertHex32,
  assertUintString, MONAD_NETWORK,
} from "../../../lib/approval-format.mjs";
export { buildApprovalData, APPROVAL_TYPES } from "../../../lib/approval-format.mjs";

const SIGNATURE = /^0x[0-9a-fA-F]{130}$/;
const check = (yes, reason) => { if (!yes) throw Error(reason); };

export async function verifyTaskPaymentApproval(approval, task, opts = {}) {
  check(approval && typeof approval === "object", "approval missing");
  const { signature, ...quote } = approval;
  check(typeof signature === "string" && SIGNATURE.test(signature), "invalid EOA signature");
  const typed = buildApprovalData(quote);
  const now = opts.nowSeconds === undefined ? BigInt(Math.floor(Date.now() / 1000)) : BigInt(opts.nowSeconds);
  check(typed.message.expiresAt > now, "payment approval expired");
  check(typed.message.expiresAt <= now + 86400n, "approval expiry exceeds 24-hour limit");
  check(task && task.status === "Created", "task must exist in Created state");
  check(assertHex32(task.taskId, "chain taskId") === typed.message.taskId, "task ID mismatch");
  check(assertHex32(task.descriptionHash, "chain descriptionHash") === typed.message.descriptionHash, "task description mismatch");
  if (BigInt(task.maxBudgetAtomic) !== 0n) {
    check(typed.message.amountAtomic <= BigInt(task.maxBudgetAtomic), "exceeds task budget");
  }
  if (opts.expectedPayTo) {
    check(assertAddress(opts.expectedPayTo, "expected payee") === typed.message.payTo, "unapproved payee");
  }
  const valid = await verifyTypedData({
    address: typed.address, domain: typed.domain, types: typed.types,
    primaryType: typed.primaryType, message: typed.message, signature,
  });
  check(valid, "payer EIP-712 signature mismatch");
  return { quote, ...typed, verified: true };
}

/** Candidate only. Caller MUST additionally ensure on-chain receipt validity. */
export function bindApprovedSettlement(approval, verifiedTransfer, task, expectedPayTo) {
  const typed = buildApprovalData(approval);
  check(task.status === "Created", "task must be Created before recordPayment");
  check(verifiedTransfer && verifiedTransfer.network === MONAD_NETWORK, "incorrect settlement network");
  check(assertHex32(verifiedTransfer.taskId, "settlement taskId") === typed.message.taskId, "settlement task mismatch");
  check(assertAddress(verifiedTransfer.token, "settlement token") === typed.message.token, "settlement token mismatch");
  check(assertAddress(verifiedTransfer.payer, "settlement payer") === typed.address, "settlement payer mismatch");
  check(assertAddress(verifiedTransfer.payTo, "settlement payee") === typed.message.payTo, "settlement payee mismatch");
  check(assertAddress(expectedPayTo, "operator expected payee") === typed.message.payTo, "operator payee mismatch");
  check(assertUintString(verifiedTransfer.amountAtomic, "settlement amount") === typed.message.amountAtomic, "settlement amount mismatch");
  check(assertHex32(verifiedTransfer.settlementRef, "settlement ref") !== "0x" + "0".repeat(64), "empty settlement ref");
  return {
    method: "recordPayment(bytes32,address,uint256,bytes32)",
    contract: typed.domain.verifyingContract,
    arguments: [typed.message.taskId, typed.address, typed.message.amountAtomic.toString(), verifiedTransfer.settlementRef],
    note: "Prepared only. Recorder MUST still enforce nonce/idempotency and reorg/finality policy before broadcasting.",
  };
}
