// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {CHAIN_ID,RPC_ID,SCENARIOS,demoTask,assertTask,
 assertPaymentIdentity,publicObservation,demoIdentityRejections,summaryJson} from "../src/domain.mjs";
import {observeTask,serviceBase} from "../src/client.mjs";
import {DEMO_EVIDENCE,DEMO_DIGEST,INPUT,OUTPUT,
  canonicalEvidence,verifyDemoEvidence} from "../src/evidence.mjs";

test("Pocket mobile walkthrough has four independent stages, no paid acceptance",()=>{
 assert.deepEqual(SCENARIOS.map(x=>x.key),["created","paid","proven","unknown"]);
 for(const s of SCENARIOS){
  const t=demoTask(s.key),fact=publicObservation(t,{simulation:true});
  assertTask(t,{contract:t.contract,taskId:t.taskId});
  assert.equal(fact.paymentStatus,"PaymentUnknown");
  assert.equal(fact.chargeAgain,false);
  assert.equal(fact.rpcContacted,false);
  assert.equal(fact.publicTestnetAcceptance,"BLOCKED_NO_REVIEWED_DEPLOYMENT");
  assert.equal(fact.finalityProven,false);
  assert.equal(fact.executionAuthority,"ACP");
  assert.equal(fact.pinnedRpcIdentifier,RPC_ID);
  assert.equal(fact.chainId,CHAIN_ID);
 }
 assert.equal(demoTask("paid").status,"Paid");
 assert.equal(demoTask("proven").status,"Proven");
 assert.equal(publicObservation(demoTask("proven"),{simulation:true,evidenceChecked:true}).evidenceState,
  "DIGEST_MATCHED_ONLY");
 assert.equal(publicObservation(demoTask("unknown"),{simulation:true}).paymentStatus,"PaymentUnknown");
});
test("cross-Ledger and payer identity replays fail closed",()=>{
 const task=demoTask("proven");
 const proof={ledger:task.contract,taskId:task.taskId,chainId:CHAIN_ID,
  descriptionHash:task.descriptionHash,payer:task.payer};
 assert.equal(assertPaymentIdentity(task,proof),true);
 assert.throws(()=>assertPaymentIdentity(task,{...proof,ledger:"0x"+"aa".repeat(20)}),/CROSS_LEDGER_REJECTED/);
 assert.throws(()=>assertPaymentIdentity(task,{...proof,payer:"0x"+"bb".repeat(20)}),/PAYER_IDENTITY_REJECTED/);
 assert.throws(()=>assertPaymentIdentity(task,{...proof,chainId:1}),/WRONG_CHAIN_REJECTED/);
 assert.deepEqual(demoIdentityRejections().map(x=>x.result),[
  "CROSS_LEDGER_REJECTED","PAYER_IDENTITY_REJECTED"
 ]);
});
test("canonical demo evidence is SHA-256 reproducible without external services",async()=>{
 const sha=txt=>createHash("sha256").update(txt).digest("hex");
 const r=await verifyDemoEvidence(async str=>sha(str));
 assert.equal(r.digest,DEMO_DIGEST);
 assert.equal(r.source,"LOCAL_SIMULATION");
 assert.equal(r.paymentStatus,"PaymentUnknown");
 assert.equal(sha(canonicalEvidence(DEMO_EVIDENCE)),DEMO_DIGEST.slice(2));
 assert.equal("0x"+sha(INPUT),DEMO_EVIDENCE.inputSha256);
 assert.equal("0x"+sha(OUTPUT),DEMO_EVIDENCE.outputSha256);
 assert.throws(()=>canonicalEvidence({...DEMO_EVIDENCE,evil:true}),/EVIDENCE_SCHEMA_MISMATCH/);
});
test("summary.json is explicit about demo source, nonce unknown and no charge",()=>{
 const report=JSON.parse(summaryJson(demoTask("proven"),{simulation:true,evidenceChecked:true}));
 assert.equal(report.schemaVersion,"agentledger.pocket.summary.v1");
 assert.equal(report.source,"LOCAL_SIMULATION");
 assert.equal(report.paymentStatus,"PaymentUnknown");
 assert.equal(report.paymentNonce,null);
 assert.equal(report.reviewedDeployment,false);
 assert.equal(report.finalityProven,false);
 assert.equal(report.rpcContacted,false);
 assert.equal(report.evidenceState,"DIGEST_MATCHED_ONLY");
 assert.equal(report.chargeAgain,false);
});
test("GET-only verifier URL checks and strict task identity prevent forged sources",async()=>{
 for(const input of ["http://example.org","http://user:pass@localhost:4321",
  "file:///tmp/foo","https://example.org/x?q=1","https://example.org/#x"]){
  assert.throws(()=>serviceBase(input),/UNTRUSTED_VERIFIER_URL/);
 }
 assert.equal(serviceBase("https://api.example.org/path"),"https://api.example.org");
 assert.equal(serviceBase("http://127.0.0.1:4321"),"http://127.0.0.1:4321");
 const task=demoTask("proven");
 const received=[];
 const fake=async (url,opts)=>{received.push({url,opts});return {ok:true,json:async()=>task}};
 const got=await observeTask("https://api.example.org",task.contract,task.taskId,fake);
 assert.equal(got.taskId,task.taskId);
 assert.equal(received[0].opts.method,"GET");
 assert.equal(received[0].opts.headers.accept,"application/json");
 assert.match(received[0].url,/\/api\/task\?/);
 await assert.rejects(()=>observeTask("https://api.example.org",task.contract,task.taskId,
  async()=>({ok:true,json:async()=>({...task,contract:"0x"+"ef".repeat(20)})})),/CROSS_LEDGER_REJECTED/);
 await assert.rejects(()=>observeTask("https://api.example.org",task.contract,task.taskId,
  async()=>({ok:true,json:async()=>({...task,chainId:1})})),/TASK_IDENTITY_OR_FIELDS_INVALID/);
 await assert.rejects(()=>observeTask("https://api.example.org",task.contract,task.taskId,
  async()=>({ok:false})),/CHAIN_READ_UNAVAILABLE/);
});
