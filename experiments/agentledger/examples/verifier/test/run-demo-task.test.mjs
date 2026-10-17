// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runDemoTask, calculateTextStats } from "../tools/run-demo-task.mjs";
import { evidenceHash, fileHash } from "../../../tools/evidence.mjs";

const TASK_ID = "0x" + "56".repeat(32);
const LEDGER = "0x" + "12".repeat(20);
const sha = bytes => "0x" + createHash("sha256").update(bytes).digest("hex");

function paths(t) {
  const dir = mkdtempSync(join(tmpdir(), "agentledger-runtime-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const inputPath = join(dir, "input.txt");
  const outputPath = join(dir, "result.json");
  const evidencePath = join(dir, "evidence.json");
  writeFileSync(inputPath, "One two\nthree four\n");
  return { contract:LEDGER,taskId:TASK_ID,inputPath,outputPath,evidencePath };
}
function stub(args, status="Paid", digest=sha(readFileSync(args.inputPath))) {
  let reads = 0;
  return {
    chain: {},
    readTask: async () => {
      reads += 1;
      return { status, taskId:TASK_ID, descriptionHash:digest };
    },
    count: () => reads,
  };
}
test("requires Paid task and binds input bytes to descriptionHash", async t => {
  const args = paths(t);
  const d = stub(args);
  const result = await runDemoTask(args,d);
  assert.equal(d.count(),1);
  assert.equal(result.status,"EXECUTED_OFFCHAIN_NOT_ANCHORED");
  assert.equal(result.taskId,TASK_ID);
  const output = JSON.parse(readFileSync(args.outputPath,"utf8"));
  assert.equal(output.wordCount,4);
  assert.equal(output.lineCount,2);
  assert.equal(output.inputSha256,fileHash(args.inputPath));
  const evidence = JSON.parse(readFileSync(args.evidencePath,"utf8"));
  assert.equal(evidenceHash(evidence),result.evidenceHash);
  assert.equal(evidence.inputSha256,fileHash(args.inputPath));
  assert.equal(evidence.outputSha256,fileHash(args.outputPath));
  await assert.rejects(() => runDemoTask(args,d),/already exists/);
  assert.equal(d.count(),1, "do not re-execute already-written output");
});
test("unpaid task cannot produce any output files", async t => {
  const args = paths(t);
  await assert.rejects(() => runDemoTask(args,stub(args,"Created")),/not Paid/);
  assert.equal(existsSync(args.outputPath),false);
  assert.equal(existsSync(args.evidencePath),false);
});
test("wrong descriptionHash prevents executing or writing", async t => {
  const args = paths(t);
  await assert.rejects(() => runDemoTask(args,stub(args,"Paid","0x"+"ff".repeat(32))),/input hash/);
  assert.equal(existsSync(args.outputPath),false);
  assert.equal(existsSync(args.evidencePath),false);
});
test("invalid input rejects empty or binary bytes", async () => {
  assert.throws(() => calculateTextStats(new Uint8Array()),/nonempty/);
  assert.throws(() => calculateTextStats(Uint8Array.from([0xff,0xfe])),/encoded data|valid/i);
  assert.throws(() => calculateTextStats(Buffer.alloc(1024*1024+1,65)),/1 MiB/);
});
