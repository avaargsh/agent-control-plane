// SPDX-License-Identifier: Apache-2.0
// Canonical agentledger.evidence.v1
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { assertHex32, type EvidenceV1, SCHEMA_EVIDENCE_V1 } from "@agentledger/core";

const FIELDS = [
  "completedAt", "executor", "inputSha256", "modelOrTool",
  "outputSha256", "schemaVersion", "taskId",
];

const hashBytes = (bytes: Uint8Array): string =>
  "0x" + createHash("sha256").update(bytes).digest("hex");

function requireHash(value: unknown, label: string): string {
  assertHex32(value, label);
  return value as string;
}

function canonicalEvidence(value: unknown): string {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("evidence must be an object");
  }
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  if (keys.length !== FIELDS.length || keys.some((key, i) => key !== FIELDS[i])) {
    throw new Error("unexpected or missing evidence fields");
  }
  if (obj.schemaVersion !== SCHEMA_EVIDENCE_V1) {
    throw new Error("unsupported schema version");
  }
  for (const key of ["taskId", "inputSha256", "outputSha256"]) {
    requireHash(obj[key], key);
  }
  for (const key of ["executor", "modelOrTool"]) {
    const v = obj[key];
    if (typeof v !== "string" || !v.trim() || v.length > 128) {
      throw new Error(`${key} must be a nonempty string <=128 characters`);
    }
  }
  const ts = obj.completedAt;
  if (typeof ts !== "string" || Number.isNaN(Date.parse(ts)) ||
      new Date(ts).toISOString() !== ts) {
    throw new Error("completedAt must be an ISO UTC timestamp with milliseconds");
  }
  const ordered: Record<string, unknown> = {};
  for (const key of FIELDS) ordered[key] = obj[key];
  return JSON.stringify(ordered);
}

function evidenceHash(evidence: unknown): string {
  return hashBytes(Buffer.from(canonicalEvidence(evidence), "utf8"));
}

function fileHash(path: string): string {
  return hashBytes(readFileSync(path));
}

interface BuildEvidenceInput {
  taskId: string;
  inputPath: string;
  outputPath: string;
  executor: string;
  modelOrTool: string;
  completedAt: string;
}

function buildEvidence(input: BuildEvidenceInput): EvidenceV1 {
  return {
    completedAt: input.completedAt,
    executor: input.executor,
    inputSha256: fileHash(input.inputPath),
    modelOrTool: input.modelOrTool,
    outputSha256: fileHash(input.outputPath),
    schemaVersion: SCHEMA_EVIDENCE_V1,
    taskId: assertHex32(input.taskId, "taskId"),
  };
}

export { canonicalEvidence, evidenceHash, fileHash, buildEvidence };
export type { BuildEvidenceInput };
