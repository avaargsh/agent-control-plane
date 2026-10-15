#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Independent no-signing readback for paid / pending-to-be-recorded tasks.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { reconcileTask } from "../lib/reconcile-task.mjs";

async function main(args) {
  if (args.length !== 3) {
    throw Error("usage: node tools/reconcile-task.mjs APPROVAL_JSON PENDING_RECEIPT_JSON EXPECTED_PAYEE");
  }
  const [approvalPath, receiptPath, expectedPayTo] = args;
  const approval = JSON.parse(readFileSync(approvalPath, "utf8"));
  const pendingReceipt = JSON.parse(readFileSync(receiptPath, "utf8"));
  const result = await reconcileTask({approval, pendingReceipt, expectedPayTo});
  console.log(JSON.stringify(result, null, 2));
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch(error => {
    console.error("RECONCILIATION UNKNOWN, NO RECHARGE: " + error.message);
    process.exitCode = 1;
  });
}
