// SPDX-License-Identifier: Apache-2.0
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { assertHex32, type EvidenceV1 } from "@agentledger/core";
import { evidenceHash } from "./create.js";

const hashBytes = (bytes: Uint8Array): string =>
  "0x" + createHash("sha256").update(bytes).digest("hex");

function requireHash(value: unknown, label: string): string {
  assertHex32(value, label);
  return value as string;
}

function hashFile(path: string): string {
  return hashBytes(readFileSync(path));
}

function verifyEvidence(
  evidence: unknown,
  onchainHash: string,
  options?: { inputPath?: string; outputPath?: string },
): { computed: string; matched: boolean } {
  requireHash(onchainHash, "onchain hash");
  const computed = evidenceHash(evidence);
  const matched = computed === onchainHash.toLowerCase();
  if (options?.inputPath || options?.outputPath) {
    if (!options.inputPath || !options.outputPath) {
      throw new Error("input and output files must both be supplied");
    }
    const doc = evidence as EvidenceV1;
    const inputHash = hashFile(options.inputPath);
    const outputHash = hashFile(options.outputPath);
    if (doc.inputSha256 !== inputHash || doc.outputSha256 !== outputHash) {
      throw new Error("input/output digest mismatch");
    }
  }
  return { computed, matched };
}

export { verifyEvidence, hashBytes };
