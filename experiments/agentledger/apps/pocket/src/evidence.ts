// SPDX-License-Identifier: Apache-2.0
// Portable, actual SHA-256 evidence verifier shared by Expo iOS and Web.
// No browser-only SubtleCrypto, no wallet, no remote upload.
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex, utf8ToBytes } from "@noble/hashes/utils.js";

export const DEMO_INPUT="Demo input only: summarize local evidence.\n";
export const DEMO_OUTPUT="Local summary: evidence is inspectable, payment unverified.\n";
export const DEMO_EXPECTED_HASH="0xde418a64a1a5da534393a20b408726b39b0c83092a142f66610fd0f8ab726649";
const HEX32=/^0x[0-9a-f]{64}$/;
const FIELDS=["completedAt","executor","inputSha256","modelOrTool","outputSha256","schemaVersion","taskId"] as const;
type EvidenceKey=typeof FIELDS[number];
export type CanonicalEvidence=Record<EvidenceKey,string>;

function fail(message:string):never{throw Error(message);}
export function sha256Hex(value:string|Uint8Array):string {
  return "0x"+bytesToHex(sha256(typeof value==="string"?utf8ToBytes(value):value));
}
export function canonicalEvidence(value:unknown):string {
  if(!value||typeof value!=="object"||Array.isArray(value))fail("Evidence must be a JSON object");
  const src=value as Record<string,unknown>;
  const sorted=Object.keys(src).sort();
  if(sorted.length!==FIELDS.length||sorted.some((key,index)=>key!==FIELDS[index]))
    fail("Unexpected or missing Evidence fields");
  if(src.schemaVersion!=="agentledger.evidence.v1")fail("Unsupported Evidence version");
  for(const field of ["taskId","inputSha256","outputSha256"])
    if(typeof src[field]!=="string"||!HEX32.test(src[field] as string))fail("Invalid "+field);
  for(const field of ["executor","modelOrTool"])
    if(typeof src[field]!=="string"||!(src[field] as string).trim()||
       (src[field] as string).length>128)fail("Invalid "+field);
  const completedAt=src.completedAt;
  if(typeof completedAt!=="string"||!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(completedAt)||
     !Number.isFinite(Date.parse(completedAt))||
     new Date(completedAt).toISOString()!==completedAt)fail("Invalid completedAt timestamp");
  const result:Record<string,string>={};
  for(const field of FIELDS)result[field]=src[field] as string;
  return JSON.stringify(result);
}
export function verifyEvidenceBytes(
  evidence:unknown,{taskId,evidenceHash,status}:{
    taskId:string;evidenceHash:string;status:string;
  },inputBytes?:Uint8Array,outputBytes?:Uint8Array,
){
  if(status!=="Proven")fail("Chain task is not Proven");
  if(!HEX32.test(taskId)||!HEX32.test(evidenceHash))fail("Invalid on-chain task");
  const normalized=canonicalEvidence(evidence);
  const data=evidence as CanonicalEvidence;
  if(data.taskId!==taskId.toLowerCase())fail("Evidence task ID does not match on-chain task ID");
  const digest=sha256Hex(normalized);
  if(digest!==evidenceHash.toLowerCase())fail("Evidence SHA-256 mismatch");
  if(inputBytes&&sha256Hex(inputBytes)!==data.inputSha256)fail("Input bytes SHA-256 mismatch");
  if(outputBytes&&sha256Hex(outputBytes)!==data.outputSha256)fail("Output bytes SHA-256 mismatch");
  return {match:true,digest,inputChecked:inputBytes!==undefined,outputChecked:outputBytes!==undefined} as const;
}
export function makeDemoEvidence(taskId:string):CanonicalEvidence{
 return {
  completedAt:"2026-10-16T00:00:00.000Z",
  executor:"local-fixture",
  inputSha256:sha256Hex(DEMO_INPUT),
  modelOrTool:"deterministic-demo",
  outputSha256:sha256Hex(DEMO_OUTPUT),
  schemaVersion:"agentledger.evidence.v1",
  taskId,
 };
}
export function verifyDemoFixture(taskId:string){
  const evidence=makeDemoEvidence(taskId);
  // The expected hash is a precomputed independent reference, NOT derived
  // from the same runtime calculation for both operands.
  return verifyEvidenceBytes(evidence,
    {taskId,evidenceHash:DEMO_EXPECTED_HASH,status:"Proven"},
    utf8ToBytes(DEMO_INPUT),utf8ToBytes(DEMO_OUTPUT));
}
