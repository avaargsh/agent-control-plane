#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const FIELDS = [
  "completedAt", "executor", "inputSha256", "modelOrTool",
  "outputSha256", "schemaVersion", "taskId",
];
const BYTES32 = /^0x[0-9a-f]{64}$/;
const hashBytes = (bytes) => "0x" + createHash("sha256").update(bytes).digest("hex");

function requireHash(value, label) {
  if (typeof value !== "string" || !BYTES32.test(value)) {
    throw new Error(label + " must be a lowercase 0x-prefixed bytes32");
  }
}

// Canonical v1 schema: exact fixed field set, ASCII keys sorted as in FIELDS,
// UTF-8 JSON without insignificant whitespace, no floating point values.
export function canonicalEvidence(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("evidence must be an object");
  }
  const keys = Object.keys(value).sort();
  if (keys.length !== FIELDS.length || keys.some((key, i) => key !== FIELDS[i])) {
    throw new Error("unexpected or missing evidence fields");
  }
  if (value.schemaVersion !== "agentledger.evidence.v1") {
    throw new Error("unsupported schema version");
  }
  for (const key of ["taskId", "inputSha256", "outputSha256"]) {
    requireHash(value[key], key);
  }
  for (const key of ["executor", "modelOrTool"]) {
    if (typeof value[key] !== "string" || !value[key].trim() || value[key].length > 128) {
      throw new Error(key + " must be a nonempty string <=128 characters");
    }
  }
  const ts = value.completedAt;
  if (typeof ts !== "string" || Number.isNaN(Date.parse(ts)) ||
      new Date(ts).toISOString() !== ts) {
    throw new Error("completedAt must be an ISO UTC timestamp with milliseconds");
  }
  const ordered = {};
  for (const key of FIELDS) ordered[key] = value[key];
  return JSON.stringify(ordered);
}

export function evidenceHash(evidence) {
  return hashBytes(Buffer.from(canonicalEvidence(evidence), "utf8"));
}

export function fileHash(path) {
  return hashBytes(readFileSync(path));
}

export function buildEvidence({ taskId, inputPath, outputPath, executor, modelOrTool, completedAt }) {
  return {
    completedAt,
    executor,
    inputSha256: fileHash(inputPath),
    modelOrTool,
    outputSha256: fileHash(outputPath),
    schemaVersion: "agentledger.evidence.v1",
    taskId,
  };
}

function main(argv) {
  const [cmd, ...args] = argv;
  if (cmd === "create") {
    if (args.length !== 4) throw new Error("create TASK_ID INPUT_FILE OUTPUT_FILE EVIDENCE_FILE");
    const [taskId, inputPath, outputPath, evidencePath] = args;
    const doc = buildEvidence({
      taskId, inputPath, outputPath,
      executor: "agentledger-demo",
      modelOrTool: "file-digest-demo",
      completedAt: new Date().toISOString(),
    });
    const canonical = canonicalEvidence(doc);
    writeFileSync(evidencePath, JSON.stringify(doc, null, 2) + "\n", { flag: "wx", mode: 0o600 });
    console.log("evidenceHash=" + hashBytes(Buffer.from(canonical, "utf8")));
    console.log("evidenceFile=" + evidencePath);
  } else if (cmd === "hash") {
    if (args.length !== 1) throw new Error("hash EVIDENCE_FILE");
    console.log(evidenceHash(JSON.parse(readFileSync(args[0], "utf8"))));
  } else if (cmd === "verify") {
    if (args.length !== 2 && args.length !== 4) {
      throw new Error("verify EVIDENCE_FILE ONCHAIN_HASH [INPUT_FILE OUTPUT_FILE]");
    }
    const [path, onchain, inputPath, outputPath] = args;
    requireHash(onchain, "onchain hash");
    const doc = JSON.parse(readFileSync(path, "utf8"));
    const computed = evidenceHash(doc);
    if (computed !== onchain) throw new Error("hash mismatch");
    if (inputPath && (doc.inputSha256 !== fileHash(inputPath) || doc.outputSha256 !== fileHash(outputPath))) {
      throw new Error("input/output digest mismatch");
    }
    console.log("MATCH " + computed);
    console.log("NOTE: hash match proves digest equality only, not task correctness or independent execution.");
  } else {
    throw new Error("usage: node tools/evidence.mjs <create|hash|verify> ...");
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { main(process.argv.slice(2)); }
  catch (e) { console.error("ERROR: " + e.message); process.exitCode = 1; }
}
