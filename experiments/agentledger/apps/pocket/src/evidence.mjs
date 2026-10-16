// SPDX-License-Identifier: Apache-2.0
// Implements the frozen AgentLedger canonical JSON v1 digest shape.
// Hashing is delegated to platform crypto (expo-crypto in native/web).
const FIELDS=["completedAt","executor","inputSha256","modelOrTool",
  "outputSha256","schemaVersion","taskId"];
const HEX32=/^0x[0-9a-f]{64}$/;
export const DEMO_DIGEST="0xde418a64a1a5da534393a20b408726b39b0c83092a142f66610fd0f8ab726649";
export const INPUT="Demo input only: summarize local evidence.\n";
export const OUTPUT="Local summary: evidence is inspectable, payment unverified.\n";
export const DEMO_EVIDENCE=Object.freeze({
  completedAt:"2026-10-16T00:00:00.000Z",
  executor:"local-fixture",
  inputSha256:"0x14482d3d6d20325d38169cf90e09115a2ecd1c0e5b0646a4e0b6a65b364e5212",
  modelOrTool:"deterministic-demo",
  outputSha256:"0x46b87415de32cb2354599819ca02ce0177987f58c4be2c0027a65ef2eb35f39a",
  schemaVersion:"agentledger.evidence.v1",
  taskId:"0x"+"23".repeat(32),
});
export function canonicalEvidence(value) {
  if(!value || typeof value!=="object" || Array.isArray(value))throw Error("EVIDENCE_NOT_OBJECT");
  const keys=Object.keys(value).sort();
  if(keys.length!==FIELDS.length || keys.some((key,i)=>key!==FIELDS[i]))throw Error("EVIDENCE_SCHEMA_MISMATCH");
  if(value.schemaVersion!=="agentledger.evidence.v1")throw Error("EVIDENCE_VERSION_UNSUPPORTED");
  for(const k of ["taskId","inputSha256","outputSha256"])if(!HEX32.test(value[k]))throw Error("EVIDENCE_INVALID_"+k);
  for(const k of ["executor","modelOrTool"])if(typeof value[k]!=="string"||!value[k].trim()||value[k].length>128)throw Error("EVIDENCE_INVALID_"+k);
  if(typeof value.completedAt!=="string" ||
    !Number.isFinite(Date.parse(value.completedAt)) ||
    new Date(value.completedAt).toISOString()!==value.completedAt)throw Error("EVIDENCE_BAD_TIMESTAMP");
  const normalized={};
  for(const key of FIELDS)normalized[key]=value[key];
  return JSON.stringify(normalized);
}
export async function verifyDemoEvidence(sha256){
  // This is a local synthetic example; it never verifies funded Testnet.
  const hash=async data=>"0x"+(await sha256(data)).toLowerCase();
  const evidence=DEMO_EVIDENCE;
  if(await hash(INPUT)!==evidence.inputSha256)throw Error("INPUT_BYTES_MISMATCH");
  if(await hash(OUTPUT)!==evidence.outputSha256)throw Error("OUTPUT_BYTES_MISMATCH");
  const digest=await hash(canonicalEvidence(evidence));
  if(digest!==DEMO_DIGEST)throw Error("EVIDENCE_DIGEST_MISMATCH");
  return {digest,inputChecked:true,outputChecked:true,match:true,
    source:"LOCAL_SIMULATION",paymentStatus:"PaymentUnknown",finalityProven:false};
}
