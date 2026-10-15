// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { buildApprovalData } from "../lib/payment-approval.mjs";
import { prepareRecord, reserveAdmission } from "../tools/prepare-record.mjs";

const ledger = "0x" + "33".repeat(20);
const payTo = "0x" + "44".repeat(20);
const taskId = "0x" + "55".repeat(32);
const descriptionHash = "0x" + "66".repeat(32);
const settlementRef = "0x" + "77".repeat(32);
const tx = "0x" + "88".repeat(32);
const usdcContract = "0x534b2f3A21130d7a60830c2Df862319e593943A3";

async function signed() {
  const signer = privateKeyToAccount(generatePrivateKey());
  const approval = {
    schemaVersion: "agentledger.task-payment.v1",
    ledger, chainId: 10143, taskId, descriptionHash, token: usdcContract,
    payTo, payer: signer.address, amountAtomic: "1000",
    expiresAt: String(Math.floor(Date.now() / 1000) + 3600),
    nonce: "0x" + "99".repeat(32)
  };
  const typed = buildApprovalData(approval);
  approval.signature = await signer.signTypedData(typed);
  return approval;
}
function fixture(approval, journalPath, overrides = {}) {
  let reads = 0, settlements = 0;
  const task = {
    taskId, descriptionHash, status: "Created", maxBudgetAtomic: "1000"
  };
  const receipt = {
    taskId, transaction: tx, payer: approval.payer, payTo, token: usdcContract,
    network: "eip155:10143", amountAtomic: "1000",
    settlementRef
  };
  return {
    deps: {
      chain: {},
      readTask: async () => { reads++; return task; },
      verifySettlement: async () => { settlements++; return { ...receipt, ...overrides }; },
      reserve: candidate => reserveAdmission(candidate, { journalPath })
    },
    counters: () => [reads, settlements]
  };
}
test("signature + receipt produce durable, one-time recorder candidate", async t => {
  const dir = mkdtempSync(join(tmpdir(), "agentledger-admission-"));
  t.after(() => rmSync(dir, { recursive:true, force:true }));
  const journalPath = join(dir, "admission.db");
  const approval = await signed();
  const harness = fixture(approval, journalPath);
  const args = { approval, pendingReceipt: { transaction:tx }, expectedPayTo:payTo };
  const first = await prepareRecord(args, harness.deps);
  assert.equal(first.status, "PREPARED_FOR_RECORDER_REVIEW_NOT_SUBMITTED");
  assert.equal(first.journal.result, "PREPARED_NOT_SUBMITTED");
  assert.equal(first.journal.admission.status, "PREPARED");
  assert.equal(first.recordProposal.arguments[0], taskId);
  assert.equal(first.recordProposal.arguments[3], settlementRef);
  assert.deepEqual(harness.counters(), [1, 1]);
  const second = await prepareRecord(args, harness.deps);
  assert.equal(second.journal.result, "EXISTING_NO_NEW_SUBMISSION");
  assert.equal(second.journal.admission.settlement_ref, first.journal.admission.settlement_ref);
});
test("signature tampering fails before any settlement read or admission", async t => {
  const dir = mkdtempSync(join(tmpdir(), "agentledger-deny-"));
  t.after(() => rmSync(dir, { recursive:true, force:true }));
  const approval = await signed();
  const harness = fixture(approval, join(dir, "admission.db"));
  await assert.rejects(
    prepareRecord({ approval:{...approval,amountAtomic:"999"}, pendingReceipt:{}, expectedPayTo:payTo }, harness.deps),
    /signature/
  );
  assert.deepEqual(harness.counters(), [1,0]);
});
test("receipt amount mismatch fails before admission", async t => {
  const dir = mkdtempSync(join(tmpdir(), "agentledger-deny-"));
  t.after(() => rmSync(dir, { recursive:true, force:true }));
  const approval = await signed();
  const harness = fixture(approval, join(dir,"admission.db"), { amountAtomic:"999" });
  await assert.rejects(prepareRecord({
    approval,pendingReceipt:{transaction:tx},expectedPayTo:payTo
  },harness.deps),/amount mismatch/);
  assert.deepEqual(harness.counters(),[1,1]);
});
