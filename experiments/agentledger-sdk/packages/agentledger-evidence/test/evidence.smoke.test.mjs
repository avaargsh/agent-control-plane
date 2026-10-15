// SPDX-License-Identifier: Apache-2.0
import { strictEqual, ok } from "node:assert";
import {
  canonicalEvidence, evidenceHash, buildEvidence, verifyEvidence,
} from "../dist/index.js";

// Build a temp fixture on the fly.
import { writeFileSync, mkdtempSync } from "node:fs";
import { join } from "node:path";

const dir = mkdtempSync("agentledger-evidence-test-");
const inputPath = join(dir, "input.txt");
const outputPath = join(dir, "output.txt");
writeFileSync(inputPath, "hello agentledger");
writeFileSync(outputPath, "hi agentledger");

const doc = buildEvidence({
  taskId: "0x" + "01".repeat(32),
  inputPath, outputPath,
  executor: "agentledger-demo",
  modelOrTool: "file-digest-demo",
  completedAt: "2026-09-30T10:00:00.000Z",
});

const canonical = canonicalEvidence(doc);
const hash = evidenceHash(doc);
strictEqual(typeof canonical, "string");
strictEqual(typeof hash, "string");
ok(hash.startsWith("0x"));
ok(hash.length === 66);

const { computed, matched } = verifyEvidence(doc, hash);
strictEqual(computed, hash);
strictEqual(matched, true);

console.log("agentledger-evidence: smoke OK");
