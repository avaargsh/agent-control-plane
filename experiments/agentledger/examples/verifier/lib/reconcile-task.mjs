// SPDX-License-Identifier: Apache-2.0
// Fail-closed, READ-ONLY reconstruction after paid x402 responses are lost.
// Reconciliation NEVER signs, charges, deletes reservations or broadcasts.
import { hashTypedData, verifyTypedData } from "viem";
import { buildApprovalData, assertAddress, assertHex32, assertUintString } from "../../../lib/approval-format.mjs";
import { inspectPrecharge } from "../tools/prepare-record.mjs";
import { makeClient, readTask } from "./task-reader.mjs";
import { verifyLive } from "../../../tools/settlement.mjs";

const check = (ok, reason) => { if (!ok) throw Error(reason); };
const ZERO = "0x" + "0".repeat(64);

export function validateReconciliation(approval, task, transfer, reservation, expectedPayTo) {
  const typed = buildApprovalData(approval);
  check(task?.taskId === typed.message.taskId, "chain task identity mismatch");
  check(task?.contract?.toLowerCase() === typed.domain.verifyingContract, "chain ledger mismatch");
  check(task?.descriptionHash === typed.message.descriptionHash, "chain description mismatch");
  check(assertAddress(expectedPayTo, "expected payee") === typed.message.payTo, "expected payee mismatch");
  check(assertHex32(transfer?.taskId, "transfer task") === typed.message.taskId, "transfer task mismatch");
  check(assertAddress(transfer.payer, "transfer payer") === typed.address, "transfer payer mismatch");
  check(assertAddress(transfer.payTo, "transfer payee") === typed.message.payTo, "transfer payee mismatch");
  check(assertAddress(transfer.token, "transfer asset") === typed.message.token, "transfer asset mismatch");
  check(assertUintString(transfer.amountAtomic, "transfer amount") === typed.message.amountAtomic, "transfer amount mismatch");
  const paymentNonce = assertHex32(transfer.paymentNonce, "consumed payment nonce");
  const expectedDigest = hashTypedData({
    domain: typed.domain, types: typed.types,
    primaryType: typed.primaryType, message: typed.message,
  }).toLowerCase();
  const row = reservation?.admission;
  check(reservation?.result === "PRECHARGE_RECORD_FOUND" &&
    row?.status === "RESERVED_RECONCILE_ONLY", "no reserved pre-charge attempt");
  check(row.ledger === typed.domain.verifyingContract &&
    row.task_id === typed.message.taskId &&
    row.payer === typed.address &&
    row.approval_nonce === typed.message.nonce &&
    row.quote_digest === expectedDigest &&
    row.payment_nonce === paymentNonce,
    "precharge reservation is not bound to the signed quote and consumed nonce");

  if (BigInt(task.maxBudgetAtomic) !== 0n) {
    check(typed.message.amountAtomic <= BigInt(task.maxBudgetAtomic), "task budget mismatch");
  }
  const settlementRef = assertHex32(transfer.settlementRef, "settlementRef");
  check(settlementRef !== ZERO, "zero settlement reference");
  if (task.status === "Created") {
    // Creator/payee status not changed; this is a human-reviewed write candidate.
    check(task.amountAtomic === "0" &&
      task.settlementRef === ZERO, "Created task carries inconsistent payment state");
    return {
      classification: "SETTLEMENT_OBSERVED_LEDGER_CREATED",
      manualAction: "Reconfirm Monad finality and recorder authority before manually calling recordPayment; NEVER pay again.",
    };
  }
  if (task.status === "Paid" || task.status === "Proven") {
    check(assertAddress(task.payer, "ledger payer") === typed.address &&
      task.amountAtomic === typed.message.amountAtomic.toString() &&
      task.settlementRef === settlementRef,
      "LEDGER_CONFLICT: recorded payment does not match consumed x402 authorization");
    return {
      classification: task.status === "Proven" ? "PROVEN_PAYMENT_MATCH" : "PAID_PAYMENT_MATCH",
      manualAction: task.status === "Proven" ?
        "Payment record matches. Verify Evidence bytes separately; no replay." :
        "Payment record matches. Do not record or pay again; inspect executor/Evidence state.",
    };
  }
  throw Error("unknown ledger task status; reconcile manually without resubmission");
}

/**
 * Independent read-only network lookups. Expired historical EIP-712 approvals
 * remain verifiable after payment; expiry alone cannot prove they were valid
 * *at charge time*. The precharge record is a local assertion, not a secure
 * timestamp, and must be reviewed together with actual chain evidence.
 */
export async function reconcileTask({approval, pendingReceipt, expectedPayTo}, deps={}) {
  const typed = buildApprovalData(approval);
  check(typeof approval.signature === "string" &&
    /^0x[0-9a-fA-F]{130}$/.test(approval.signature), "missing payer signature");
  check(await (deps.verifySignature || verifyTypedData)({
    address: typed.address, domain: typed.domain, types: typed.types,
    primaryType: typed.primaryType, message: typed.message,
    signature: approval.signature,
  }), "payer task signature invalid");
  // DO NOT demand Created or unexpired quote for post-payment reconstruction.
  const chain = deps.chain || makeClient(process.env.MONAD_RPC_URL);
  const task = await (deps.readTask || readTask)(chain, approval.ledger, approval.taskId);
  check(task.chainId === 10143, "wrong chain for task");
  check(pendingReceipt?.paymentNonce &&
    typeof pendingReceipt.paymentNonce === "string", "missing pending EIP-3009 nonce");
  const verified = await (deps.verifySettlement || verifyLive)(
    pendingReceipt, process.env.MONAD_RPC_URL
  );
  check(verified.paymentNonce === pendingReceipt.paymentNonce.toLowerCase(),
    "nonce not consumed in verified USDC transaction");
  const precharge = await (deps.inspectPrecharge || inspectPrecharge)(verified.paymentNonce);
  const decision = validateReconciliation(approval, task, verified, precharge, expectedPayTo);
  return {
    schemaVersion: "agentledger.reconciliation.v1",
    status: "READ_ONLY_NO_RECHARGE",
    taskId: typed.message.taskId,
    classification: decision.classification,
    manualAction: decision.manualAction,
    chainStatus: task.status,
    settlementRef: verified.settlementRef,
    paymentNonce: verified.paymentNonce,
    observedTransaction: verified.transaction,
    blockNumber: verified.blockNumber,
    confirmations: verified.confirmations,
    warning: "Block depth is not finality; precharge SQLite timestamp is not a trusted time attestation. No payment or chain mutation occurred.",
  };
}
