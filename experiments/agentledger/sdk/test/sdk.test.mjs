// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  SDK_VERSION, SDK_CAPABILITIES, createAgentLedgerVerifier,
  evidenceHash, PHASE0_SCHEMA,
} from "../dist/index.mjs";
const CONTRACT="0x"+"ab".repeat(20);
const ID="0x"+"11".repeat(32);
const ZERO="0x"+"00".repeat(32);
const payload={
  completedAt:"2026-10-16T00:00:00.000Z",
  executor:"demo-tool",inputSha256:"0x"+"33".repeat(32),
  modelOrTool:"bounded-statistics",outputSha256:"0x"+"44".repeat(32),
  schemaVersion:"agentledger.evidence.v1",taskId:ID,
};
const fake=(digest)=>({
  getChainId:async()=>10143,
  getBytecode:async()=> "0x6001",
  readContract:async()=>({
    creator:CONTRACT,payer:CONTRACT,amount:1000n,maxBudget:2000n,
    descriptionHash:"0x"+"22".repeat(32),evidenceHash:digest,
    settlementRef:"0x"+"55".repeat(32),status:3,
    createdAt:1n,paidAt:2n,provenAt:3n,
  }),
});
test("SDK alpha is read-only, unpublishable payment and Agent-run API",()=>{
  assert.equal(SDK_VERSION,"0.2.0-alpha.1");
  for(const key of ["charge","sign","mutateLedger","manageAcp","executeAgent","proveChainFinality"]){
    assert.equal(SDK_CAPABILITIES[key],false);
  }
  const sdk=createAgentLedgerVerifier({client:fake(ZERO)});
  assert.equal("recordPayment" in sdk,false);
  assert.equal("submitEvidence" in sdk,false);
  assert.equal("retryPayment" in sdk,false);
  assert.equal(Object.isFrozen(sdk),true);
});
test("recorder Proven alone remains PaymentUnknown in SDK projection",async()=>{
  const sdk=createAgentLedgerVerifier({client:fake(ZERO)});
  const out=await sdk.getStatus(CONTRACT,ID);
  assert.equal(out.schemaVersion,PHASE0_SCHEMA);
  assert.equal(out.state,"PaymentUnknown");
  assert.equal(out.payment.eventIndependentlyChecked,false);
  assert.equal(out.retryPolicy.chargeAgain,false);
});
test("independently matching digest never pretends payment or execution correctness",async()=>{
  const digest=await evidenceHash(payload);
  const sdk=createAgentLedgerVerifier({client:fake(digest)});
  const out=await sdk.verifyEvidence(CONTRACT,ID,payload);
  assert.equal(out.match,true);
  assert.equal(out.paymentVerified,false);
  assert.equal(out.paymentStatus,"PaymentUnknown");
  assert.equal(out.evidenceFact,"HashMatchedToOnchainAnchor");
  await assert.rejects(
    ()=>sdk.verifyEvidence(CONTRACT,ID,{...payload,executor:"forged"}),
    /SHA-256 mismatch/
  );
});
test("cross-chain reads are refused, receipt calls with injected clients are forbidden",async()=>{
  const sdk=createAgentLedgerVerifier({client:{...fake(ZERO),getChainId:async()=>1}});
  await assert.rejects(()=>sdk.getStatus(CONTRACT,ID),/wrong chain/);
  await assert.rejects(()=>sdk.verifySettlement({}),/Injected RPC clients/);
  assert.throws(()=>createAgentLedgerVerifier({client:fake(ZERO),rpcUrl:"http://127.0.0.1:3333"}),/either/);
});

test("SDK never accepts nonce-free receipt observation as independent USDC proof",async()=>{
  const sdk=createAgentLedgerVerifier({rpcUrl:"http://127.0.0.1:18545"});
  await assert.rejects(()=>sdk.verifySettlement({
    transaction:"0x"+"77".repeat(32),network:"eip155:10143"
  }),/paymentNonce required/);
  assert.equal("verifySettlementReceipt" in await import("../dist/index.mjs"),false);
});
