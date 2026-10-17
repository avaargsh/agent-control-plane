// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import {
 canonicalEvidence,DEMO_EXPECTED_HASH,DEMO_INPUT,DEMO_OUTPUT,
 makeDemoEvidence,sha256Hex,verifyDemoFixture,verifyEvidenceBytes,
} from "./evidence";
import {FACTS,exportSummary} from "./facts";
import {utf8ToBytes} from "@noble/hashes/utils.js";
const ID="0x"+"23".repeat(32);
test("mobile/Web sha256 reproduces independent official browser fixture exactly",()=>{
 const e=makeDemoEvidence(ID);
 assert.equal(sha256Hex(DEMO_INPUT),e.inputSha256);
 assert.equal(sha256Hex(DEMO_OUTPUT),e.outputSha256);
 assert.equal(sha256Hex(canonicalEvidence(e)),DEMO_EXPECTED_HASH);
 assert.equal(FACTS.evidence.evidenceHash,DEMO_EXPECTED_HASH);
 const result=verifyDemoFixture(ID);
 assert.equal(result.match,true);
 assert.equal(result.inputChecked,true);
 assert.equal(result.outputChecked,true);
 const summary=exportSummary(FACTS.evidence,true);
 assert.equal(summary.payment.observed,"PaymentUnknown");
 assert.equal(summary.rpc.contacted,false);
 assert.equal(summary.security.publicTestnetAcceptance,"BLOCKED_NO_REVIEWED_DEPLOYMENT");
});
test("canonical bytes are key-order independent and reject unknown fields",()=>{
 const evidence=makeDemoEvidence(ID);
 assert.equal(canonicalEvidence(evidence),canonicalEvidence(Object.fromEntries(Object.entries(evidence).reverse())));
 assert.throws(()=>canonicalEvidence({...evidence,untrusted:"injected"}),/Unexpected or missing/);
 assert.throws(()=>canonicalEvidence({...evidence,schemaVersion:"evil"}),/Unsupported/);
 assert.throws(()=>canonicalEvidence({...evidence,taskId:"0xdead"}),/Invalid taskId/);
 assert.throws(()=>canonicalEvidence({...evidence,completedAt:"yesterday"}),/timestamp/);
});
test("hash comparison requires Proven, task identity, digest and optional original bytes",()=>{
 const e=makeDemoEvidence(ID);
 const correct={taskId:ID,evidenceHash:DEMO_EXPECTED_HASH,status:"Proven"};
 assert.throws(()=>verifyEvidenceBytes(e,{...correct,status:"Paid"}),/not Proven/);
 assert.throws(()=>verifyEvidenceBytes(e,{...correct,taskId:"0x"+"bb".repeat(32)}),/task ID/);
 assert.throws(()=>verifyEvidenceBytes(e,{...correct,evidenceHash:"0x"+"ff".repeat(32)}),/SHA-256/);
 assert.throws(()=>verifyEvidenceBytes({...e,executor:"forged"},correct),/SHA-256 mismatch/);
 assert.throws(()=>verifyEvidenceBytes(e,correct,utf8ToBytes("tampered")),/Input bytes/);
 assert.throws(()=>verifyEvidenceBytes(e,correct,undefined,utf8ToBytes("tampered")),/Output bytes/);
 assert.equal(verifyEvidenceBytes(e,correct).inputChecked,false);
});
