// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import {utf8ToBytes} from "@noble/hashes/utils.js";
import {FILE_LIMITS,parseEvidenceBytes,readPickedDocument} from "./file-contract";
import {strictUtf8} from "./utf8";
import {makeDemoEvidence,verifyEvidenceBytes,DEMO_EXPECTED_HASH,DEMO_INPUT,DEMO_OUTPUT} from "./evidence";
const ID="0x"+"23".repeat(32);
const fixture=makeDemoEvidence(ID);
test("strict UTF-8 correctly decodes ASCII, Chinese, surrogate-pair and BOM",()=>{
 const input='{"message":"合同 ✅ 中文"}';
 assert.equal(strictUtf8(utf8ToBytes(input)),input);
 assert.equal(strictUtf8(new Uint8Array([0xef,0xbb,0xbf,...utf8ToBytes(input)])),input);
 for(const b of [[0xc0,0x80],[0xed,0xa0,0x80],[0xf4,0x90,0x80,0x80],[0xe2,0x28,0xa1],[0xf0,0x9f]]) {
  assert.throws(()=>strictUtf8(new Uint8Array(b)),/INVALID_UTF8/);
 }
});
test("binary-picked Evidence verifies actual byte array and original input/output",async()=>{
 const raw=utf8ToBytes(JSON.stringify(fixture));
 const selected=await readPickedDocument("evidence",
  async()=>({canceled:false,assets:[{name:"attestation.json",size:raw.byteLength,uri:"file:///tmp/evidence"}]}),
  async()=>raw);
 assert.ok(selected);
 assert.equal(selected.name,"attestation.json");
 const parsed=parseEvidenceBytes(selected.bytes);
 const result=verifyEvidenceBytes(parsed,
  {taskId:ID,evidenceHash:DEMO_EXPECTED_HASH,status:"Proven"},
  utf8ToBytes(DEMO_INPUT),utf8ToBytes(DEMO_OUTPUT));
 assert.equal(result.match,true);
 assert.equal(result.inputChecked,true);
 assert.equal(result.outputChecked,true);
});
test("canceled picker, missing asset, read-error and cap failures are fail closed",async()=>{
 let called=0;
 const read=async()=>{called++;return new Uint8Array([1,2]);};
 assert.equal(await readPickedDocument("input",async()=>({canceled:true,assets:null}),read),null);
 assert.equal(called,0);
 await assert.rejects(()=>readPickedDocument("evidence",async()=>({canceled:false,assets:[]}),read),/MISSING_ASSET/);
 await assert.rejects(()=>readPickedDocument("evidence",async()=>({
  canceled:false,assets:[{name:"big.json",size:FILE_LIMITS.evidence+1,uri:"file:///big"}]
 }),read),/FILE_TOO_LARGE_EVIDENCE/);
 assert.equal(called,0);
 await assert.rejects(()=>readPickedDocument("input",async()=>({
  canceled:false,assets:[{name:"fake-small",size:1,uri:"file:///bad"}]
 }),async()=>new Uint8Array(FILE_LIMITS.input+1)),/FILE_TOO_LARGE_INPUT/);
 await assert.rejects(()=>readPickedDocument("output",async()=>({
  canceled:false,assets:[{name:"broken",uri:"file:///bad"}]
 }),async()=>{throw Error("UNREADABLE");}),/UNREADABLE/);
});
test("invalid Evidence UTF-8 and malformed JSON never verify",()=>{
 assert.throws(()=>parseEvidenceBytes(new Uint8Array([0xc0,0x80])),/INVALID_UTF8/);
 assert.throws(()=>parseEvidenceBytes(utf8ToBytes("{oops")),/SyntaxError/);
 assert.throws(()=>parseEvidenceBytes(new Uint8Array(FILE_LIMITS.evidence+1)),/FILE_TOO_LARGE_EVIDENCE/);
});
