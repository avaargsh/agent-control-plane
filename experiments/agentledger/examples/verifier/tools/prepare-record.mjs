#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Read-only EIP-712 + on-chain receipt verification, followed by durable
// local admission reservation. No signer, payment, or chain write.
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { hashTypedData } from "viem";
import { makeClient, readTask } from "../lib/task-reader.mjs";
import { verifyTaskPaymentApproval, bindApprovedSettlement } from "../lib/payment-approval.mjs";
import { verifyLive } from "../../../tools/settlement.mjs";

const journalScript = fileURLToPath(new URL("../../../tools/admission-journal.py", import.meta.url));
const journalPath = fileURLToPath(new URL("../../../receipts/admission.db", import.meta.url));
const prechargeScript = fileURLToPath(new URL("../../../tools/charge-attempt-journal.py", import.meta.url));
const prechargePath = fileURLToPath(new URL("../../../receipts/charge-attempts.db", import.meta.url));

export function inspectPrecharge(paymentNonce, opts = {}) {
  if (!/^0x[0-9a-f]{64}$/.test(paymentNonce || "")) {
    throw Error("verified settlement missing EIP-3009 payment nonce");
  }
  const job = spawnSync(opts.python || "python3", [prechargeScript, "inspect", paymentNonce], {
    encoding: "utf8", maxBuffer: 1_048_576, timeout: 15_000,
    env: { ...process.env, AGENTLEDGER_CHARGE_DB: opts.dbPath ||
      process.env.AGENTLEDGER_CHARGE_DB || prechargePath },
  });
  if (job.error) throw job.error;
  if (job.status !== 0) throw Error("missing or unreadable pre-charge reservation");
  return JSON.parse(job.stdout);
}


export function reserveAdmission(candidate, opts = {}) {
  const job = spawnSync(opts.python || "python3", [journalScript, "prepare"], {
    input: JSON.stringify(candidate), encoding: "utf8", maxBuffer: 1_048_576,
    timeout: 15_000,
    env: { ...process.env, AGENTLEDGER_JOURNAL_DB: opts.journalPath ||
      process.env.AGENTLEDGER_JOURNAL_DB || journalPath },
  });
  if (job.error) throw job.error;
  if (job.status !== 0) {
    throw Error("local journal refused admission: " + (job.stderr || "").trim());
  }
  return JSON.parse(job.stdout);
}

export async function prepareRecord({approval, pendingReceipt, expectedPayTo}, deps = {}) {
  const chain = deps.chain || makeClient(process.env.MONAD_RPC_URL);
  const task = await (deps.readTask || readTask)(chain, approval.ledger, approval.taskId);
  const verifiedApproval = await (deps.verifyApproval || verifyTaskPaymentApproval)(
    approval, task, { expectedPayTo }
  );
  // Strictly re-read receipt from Monad RPC; journal files are not authority.
  const verifiedTransfer = await (deps.verifySettlement || verifyLive)(
    pendingReceipt, process.env.MONAD_RPC_URL
  );
  if (!verifiedTransfer?.paymentNonce ||
      verifiedTransfer.paymentNonce !== pendingReceipt.paymentNonce?.toLowerCase()) {
    throw Error("EIP-3009 authorization nonce absent or not anchored in transaction receipt");
  }
  const recordProposal = bindApprovedSettlement(approval, verifiedTransfer, task, expectedPayTo);
  const typed = verifiedApproval;
  const quoteDigest = hashTypedData({
    domain: typed.domain, types: typed.types,
    primaryType: typed.primaryType, message: typed.message
  }).toLowerCase();
  // Match the on-chain consumed nonce to the earlier signed authorization's
  // durable PRE-CHARGE reservation, before preparing any recorder candidate.
  const precharge = (deps.inspectPrecharge || inspectPrecharge)(verifiedTransfer.paymentNonce);
  const held = precharge?.admission;
  if (precharge?.result !== "PRECHARGE_RECORD_FOUND" ||
      held?.status !== "RESERVED_RECONCILE_ONLY" ||
      held.ledger !== approval.ledger.toLowerCase() ||
      held.task_id !== task.taskId.toLowerCase() ||
      held.payer !== approval.payer.toLowerCase() ||
      held.approval_nonce !== approval.nonce.toLowerCase() ||
      held.payment_nonce !== verifiedTransfer.paymentNonce ||
      held.quote_digest !== quoteDigest) {
    throw Error("pre-charge reservation does not match signed task and consumed payment nonce");
  }
  const candidate = {
    schemaVersion: "agentledger.admission.v1",
    ledger: approval.ledger.toLowerCase(),
    taskId: task.taskId.toLowerCase(),
    payer: verifiedTransfer.payer.toLowerCase(),
    nonce: approval.nonce.toLowerCase(),
    quoteDigest,
    settlementRef: verifiedTransfer.settlementRef.toLowerCase(),
    transaction: verifiedTransfer.transaction.toLowerCase(),
    amountAtomic: verifiedTransfer.amountAtomic
  };
  const journal = (deps.reserve || reserveAdmission)(candidate);
  return {
    status: "PREPARED_FOR_RECORDER_REVIEW_NOT_SUBMITTED",
    taskId: task.taskId, approvalSigner: approval.payer,
    transfer: verifiedTransfer, recordProposal,
    precharge: {status: held.status, paymentNonce: held.payment_nonce},
    journal,
    warning: "No chain write or exactly-once payment guarantee. UNKNOWN rows must be reconciled, not recharged."
  };
}

async function main(args) {
  if (args.length !== 3) {
    throw Error("usage: node tools/prepare-record.mjs APPROVAL_JSON PENDING_RECEIPT_JSON EXPECTED_PAYEE");
  }
  const [approvalPath, receiptPath, expectedPayTo] = args;
  const approval = JSON.parse(readFileSync(approvalPath, "utf8"));
  const pendingReceipt = JSON.parse(readFileSync(receiptPath, "utf8"));
  console.log(JSON.stringify(
    await prepareRecord({ approval, pendingReceipt, expectedPayTo }), null, 2
  ));
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch(error => {
    console.error("NOT READY: " + error.message);
    process.exitCode = 1;
  });
}
