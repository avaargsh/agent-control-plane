#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Offline signer verification + live read-only receipt check, never broadcasts.
import { readFileSync } from "node:fs";
import { makeClient, readTask } from "../lib/task-reader.mjs";
import { verifyTaskPaymentApproval, bindApprovedSettlement } from "../lib/payment-approval.mjs";
import { verifyLive } from "../../../tools/settlement.mjs";

async function main(args) {
  if (args.length !== 3) {
    throw Error("usage: node tools/prepare-record.mjs APPROVAL_JSON PENDING_RECEIPT_JSON EXPECTED_PAYEE");
  }
  const [approvalPath, receiptPath, expectedPayTo] = args;
  const approval = JSON.parse(readFileSync(approvalPath, "utf8"));
  const pendingReceipt = JSON.parse(readFileSync(receiptPath, "utf8"));
  const chain = makeClient(process.env.MONAD_RPC_URL);
  const task = await readTask(chain, approval.ledger, approval.taskId);
  await verifyTaskPaymentApproval(approval, task, { expectedPayTo });
  // Verify the pending x402 journal against its transaction hash on Monad.
  const verified = await verifyLive(pendingReceipt, process.env.MONAD_RPC_URL);
  const recordProposal = bindApprovedSettlement(approval, verified, task, expectedPayTo);
  console.log(JSON.stringify({
    status: "ELIGIBLE_FOR_RECORDER_REVIEW_NOT_SUBMITTED",
    taskId: task.taskId, approvalSigner: approval.payer,
    transfer: verified, recordProposal
  }, null, 2));
}
main(process.argv.slice(2)).catch(error => {
  console.error("NOT READY: " + error.message);
  process.exitCode = 1;
});
