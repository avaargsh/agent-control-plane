// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { createVerifierServer } from "../server.mjs";
import { normalizeTask, readTask, validateLookup } from "../lib/task-reader.mjs";
import { canonicalEvidence, evidenceHash, sha256Hex, verifyEvidence } from "../public/evidence-browser.mjs";
import { evidenceHash as cliEvidenceHash } from "../../../tools/evidence.mjs";

const CONTRACT = "0x" + "ab".repeat(20);
const PAYER = "0x" + "cd".repeat(20);
const TASK = "0x" + "12".repeat(32);
const PROOF = "0x" + "34".repeat(32);
const ZERO = "0x" + "00".repeat(32);

const task = {
  creator: CONTRACT, payer: PAYER, amount: 1000n, maxBudget: 2000n,
  descriptionHash: PROOF, evidenceHash: PROOF, settlementRef: PROOF,
  status: 3, createdAt: 100n, paidAt: 101n, provenAt: 102n
};
const evidence = {
  taskId: TASK,
  executor: "agentledger-test",
  modelOrTool: "unit-test",
  completedAt: "2026-10-15T12:00:00.000Z",
  inputSha256: "0x" + "12".repeat(32),
  outputSha256: "0x" + "34".repeat(32),
  schemaVersion: "agentledger.evidence.v1"
};

test("task reader returns correct typed status, amounts and hashes", async () => {
  const calls = [];
  const client = {
    getChainId: async () => 10143,
    getBytecode: async ({address}) => { assert.equal(address, CONTRACT); return "0x6000"; },
    readContract: async (options) => { calls.push(options); return task; }
  };
  const output = await readTask(client, CONTRACT, TASK);
  assert.equal(output.status, "Proven");
  assert.equal(output.amountAtomic, "1000");
  assert.equal(output.provenAt, "102");
  assert.equal(calls[0].functionName, "getTask");
  assert.equal(calls[0].args[0], TASK);
});
test("wrong chain, missing code and absent tasks fail closed", async () => {
  await assert.rejects(() => readTask({getChainId:async()=>1},CONTRACT,TASK), /wrong chain/);
  await assert.rejects(() => readTask({getChainId:async()=>10143,getBytecode:async()=>"0x"},CONTRACT,TASK), /no contract/);
  assert.throws(() => normalizeTask(TASK,CONTRACT,{...task,status:0}),/invalid task state/);
  assert.throws(() => validateLookup(CONTRACT,"0x123"),/invalid bytes32/);
});
test("canonical browser SHA-256 is byte-identical to the CLI", async () => {
  assert.equal(evidenceHash instanceof Function, true);
  assert.equal(await evidenceHash(evidence), cliEvidenceHash(evidence));
  assert.equal(await evidenceHash(Object.fromEntries(Object.entries(evidence).reverse())), cliEvidenceHash(evidence));
  assert.throws(() => canonicalEvidence({...evidence, injected:true}),/Unexpected/);
});
test("local verification rejects wrong task, wrong evidence, wrong raw files", async () => {
  const digest = await evidenceHash(evidence);
  const chainTask = {taskId:TASK, evidenceHash:digest, status:"Proven"};
  assert.equal((await verifyEvidence(evidence,chainTask)).match,true);
  await assert.rejects(() => verifyEvidence(evidence,{...chainTask,taskId:PROOF}),/task ID/);
  await assert.rejects(() => verifyEvidence(evidence,{...chainTask,evidenceHash:PROOF}),/SHA-256/);
  await assert.rejects(() => verifyEvidence(evidence,{...chainTask,status:"Paid"}),/not Proven/);
  await assert.rejects(() => verifyEvidence(evidence,chainTask,new TextEncoder().encode("wrong")),/Input bytes/);
  assert.equal((await sha256Hex(new TextEncoder().encode("abc"))).length,66);
});
test("HTTP service is read-only, validates request and serves content safely", async () => {
  let lookupCalls = 0;
  const server = createVerifierServer({
    lookup: async (contract,id) => {lookupCalls++;return {...normalizeTask(id,contract,task)};}
  });
  await new Promise(resolve => server.listen(0,"127.0.0.1",resolve));
  const base = "http://127.0.0.1:" + server.address().port;
  try {
    const health = await fetch(base + "/api/healthz");
    assert.equal((await health.json()).chainId,10143);
    const bad = await fetch(base + "/api/task?contract=evil&taskId=" + TASK);
    assert.equal(bad.status,400);
    assert.equal(lookupCalls,0);
    const ok = await fetch(base + "/api/task?contract=" + CONTRACT + "&taskId=" + TASK);
    assert.equal(ok.status,200);
    assert.equal((await ok.json()).status,"Proven");
    assert.equal(lookupCalls,1);
    const method = await fetch(base + "/api/task",{method:"POST"});
    assert.equal(method.status,405);
    assert.match(ok.headers.get("content-security-policy"),/default-src 'none'/);
    const traversal = await fetch(base + "/.env");
    assert.equal(traversal.status,404);
    const page = await fetch(base);
    assert.equal(page.status,200);
    assert.match(await page.text(),/AgentLedger/);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});
