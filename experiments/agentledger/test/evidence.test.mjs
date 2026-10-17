// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { canonicalEvidence, evidenceHash } from "../tools/evidence.mjs";

const example = {
  schemaVersion: "agentledger.evidence.v1",
  taskId: "0x" + "01".repeat(32),
  inputSha256: "0x" + "02".repeat(32),
  outputSha256: "0x" + "03".repeat(32),
  executor: "agentledger-demo",
  modelOrTool: "sha256-demo",
  completedAt: "2026-10-15T00:00:00.000Z",
};

test("field order does not affect digest", () => {
  const a = evidenceHash(example);
  const b = evidenceHash(Object.fromEntries(Object.entries(example).reverse()));
  assert.equal(a, b);
  assert.match(a, /^0x[0-9a-f]{64}$/);
});

test("modifying execution evidence changes hash", () => {
  assert.notEqual(evidenceHash(example), evidenceHash({ ...example, outputSha256: "0x" + "04".repeat(32) }));
});

test("rejects unknown fields and malformed hashes", () => {
  assert.throws(() => canonicalEvidence({ ...example, surprise: "oops" }));
  assert.throws(() => canonicalEvidence({ ...example, taskId: "0x1234" }));
  assert.throws(() => canonicalEvidence({ ...example, completedAt: "yesterday" }));
});

test("schema validation catches missing fields", () => {
  const { executor, ...bad } = example;
  assert.throws(() => canonicalEvidence(bad));
});
