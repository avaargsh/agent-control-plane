#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
/**
 * AgentLedger minimal OFFCHAIN tool execution, intentionally without an LLM.
 *
 * Requires a Created -> Paid ledger task, checked by read-only RPC. Input
 * SHA-256 must equal the task's on-chain descriptionHash. Produces a local
 * deterministic result and a canonical Evidence JSON for manual anchoring.
 * This is NOT independent ACP execution attestation.
 */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { makeClient, readTask } from "../lib/task-reader.mjs";
import { buildEvidence, evidenceHash } from "../../../tools/evidence.mjs";
import { textStatistics } from "../public/text-stats.mjs";

const hash = data => "0x" + createHash("sha256").update(data).digest("hex");

export function calculateTextStats(buffer) {
  const stats = textStatistics(buffer);
  return {
    schemaVersion: "agentledger.demo-tool-result.v1",
    tool: "deterministic-text-stats",
    inputSha256: hash(buffer),
    ...stats,
  };
}

export async function runDemoTask({
  contract, taskId, inputPath, outputPath, evidencePath
}, deps = {}) {
  if (!inputPath || !outputPath || !evidencePath) throw Error("paths required");
  if (inputPath === outputPath || inputPath === evidencePath || outputPath === evidencePath) {
    throw Error("input/output/evidence paths must differ");
  }
  if (existsSync(outputPath) || existsSync(evidencePath)) {
    throw Error("output/evidence already exists; stop and reconcile, never overwrite");
  }
  const input = readFileSync(inputPath);
  const result = calculateTextStats(input);
  const chain = deps.chain || makeClient(process.env.MONAD_RPC_URL);
  const lookup = deps.readTask || readTask;
  const task = await lookup(chain, contract, taskId);
  if (task.status !== "Paid") throw Error("task is not Paid on chain");
  if (task.taskId.toLowerCase() !== taskId.toLowerCase()) throw Error("taskId mismatch");
  if (task.descriptionHash.toLowerCase() !== result.inputSha256) {
    throw Error("input hash does not match on-chain task descriptionHash");
  }
  // Write exclusively; crash after the first write remains a visible incomplete
  // attempt and requires operator recovery. It does not trigger a second charge.
  writeFileSync(outputPath, JSON.stringify(result, null, 2) + "\n", {
    flag: "wx", mode: 0o600,
  });
  const evidence = buildEvidence({
    taskId: taskId.toLowerCase(), inputPath, outputPath,
    executor: "agentledger-demo-runner",
    modelOrTool: "deterministic-text-stats",
    completedAt: new Date().toISOString(),
  });
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2) + "\n", {
    flag: "wx", mode: 0o600,
  });
  return {
    status: "EXECUTED_OFFCHAIN_NOT_ANCHORED",
    taskId: evidence.taskId, outputPath, evidencePath,
    evidenceHash: evidenceHash(evidence),
    note: "Manual authorized submitEvidence is required; hash match does not prove execution correctness.",
  };
}

async function main(args) {
  if (args.length !== 5) {
    throw Error("usage: run-demo-task.mjs LEDGER_ADDRESS TASK_ID INPUT_FILE OUTPUT_FILE EVIDENCE_FILE");
  }
  console.log(JSON.stringify(await runDemoTask({
    contract: args[0], taskId: args[1],
    inputPath: args[2], outputPath: args[3], evidencePath: args[4],
  }), null, 2));
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch(error => {
    console.error("EXECUTION REFUSED: " + error.message);
    process.exitCode = 1;
  });
}
