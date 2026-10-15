// SPDX-License-Identifier: Apache-2.0
// Runs entirely in the browser. Evidence bytes are NEVER posted to the server.
const FIELDS = [
  "completedAt", "executor", "inputSha256", "modelOrTool",
  "outputSha256", "schemaVersion", "taskId"
];
const BYTES32 = /^0x[0-9a-f]{64}$/;
const fail = (reason) => { throw new Error(reason); };
export function canonicalEvidence(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("Evidence must be a JSON object");
  const keys = Object.keys(value).sort();
  if (keys.length !== FIELDS.length || keys.some((key, index) => key !== FIELDS[index])) {
    fail("Unexpected or missing Evidence fields");
  }
  if (value.schemaVersion !== "agentledger.evidence.v1") fail("Unsupported Evidence version");
  for (const field of ["taskId", "inputSha256", "outputSha256"]) {
    if (typeof value[field] !== "string" || !BYTES32.test(value[field])) fail("Invalid " + field);
  }
  for (const field of ["executor", "modelOrTool"]) {
    if (typeof value[field] !== "string" || !value[field].trim() || value[field].length > 128) fail("Invalid " + field);
  }
  const time = value.completedAt;
  if (typeof time !== "string" || Number.isNaN(Date.parse(time)) ||
      new Date(time).toISOString() !== time) fail("Invalid completedAt timestamp");
  const canonical = {};
  for (const key of FIELDS) canonical[key] = value[key];
  return JSON.stringify(canonical);
}
export async function sha256Hex(data) {
  const hash = await crypto.subtle.digest("SHA-256", data);
  return "0x" + Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("");
}
export async function evidenceHash(evidence) {
  return sha256Hex(new TextEncoder().encode(canonicalEvidence(evidence)));
}
export async function verifyEvidence(evidence, task, inputBytes = null, outputBytes = null) {
  if (!task || task.status !== "Proven") fail("Chain task is not Proven");
  if (!BYTES32.test(task.taskId) || !BYTES32.test(task.evidenceHash)) fail("Invalid on-chain task");
  const normalized = canonicalEvidence(evidence);
  if (evidence.taskId !== task.taskId.toLowerCase()) fail("Evidence task ID does not match on-chain task ID");
  const computed = await sha256Hex(new TextEncoder().encode(normalized));
  if (computed !== task.evidenceHash.toLowerCase()) fail("Evidence SHA-256 mismatch");
  if (inputBytes && await sha256Hex(inputBytes) !== evidence.inputSha256) fail("Input bytes SHA-256 mismatch");
  if (outputBytes && await sha256Hex(outputBytes) !== evidence.outputSha256) fail("Output bytes SHA-256 mismatch");
  return { match: true, digest: computed, inputChecked: inputBytes !== null, outputChecked: outputBytes !== null };
}
