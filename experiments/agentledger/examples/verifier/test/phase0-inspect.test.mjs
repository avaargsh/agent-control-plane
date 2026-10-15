// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { evidenceHash } from "../public/evidence-browser.mjs";
import { inspectPhase0 } from "../lib/phase0-inspect.mjs";

const ID="0x"+"11".repeat(32);
const LEDGER="0x"+"22".repeat(20);
const ZERO="0x"+"0".repeat(64);
const REF="0x"+"33".repeat(32);
const TASK={
  chainId:10143,contract:LEDGER,taskId:ID,status:"Proven",
  payer:"0x"+"44".repeat(20),amountAtomic:"1000",
  settlementRef:REF,descriptionHash:"0x"+"55".repeat(32),
  evidenceHash:ZERO,maxBudgetAtomic:"1000",
};
const reconciled={
  status:"READ_ONLY_NO_RECHARGE",taskId:ID,
  classification:"PROVEN_PAYMENT_MATCH",settlementRef:REF,
  paymentNonce:"0x"+"66".repeat(32),observedTransaction:"0x"+"77".repeat(32)
};
function fixture(t) {
  const dir=mkdtempSync(join(tmpdir(),"agentledger-phase0-"));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const path=join(dir,"evidence.json");
  const evidence={
    completedAt:"2026-10-15T12:00:00.000Z",executor:"unit-test",
    inputSha256:"0x"+"88".repeat(32),modelOrTool:"demo",
    outputSha256:"0x"+"99".repeat(32),schemaVersion:"agentledger.evidence.v1",taskId:ID,
  };
  writeFileSync(path,JSON.stringify(evidence));
  return {path,evidence};
}
const req=(extras={})=>({
  schemaVersion:"agentledger.phase0.inspect.v1",ledger:LEDGER,taskId:ID,
  attempt:"Reserved", ...extras,
});
test("Phase0 independently verifies actual Evidence JSON bytes, given on-chain hash",async t=>{
  const {path,evidence}=fixture(t);
  const hash=await evidenceHash(evidence);
  const task={...TASK,evidenceHash:hash};
  const result=await inspectPhase0(req({
    payment:{approval:{},pendingReceipt:{},expectedPayTo:LEDGER},
    evidence:{file:path},
  }),{
    chain:{},readTask:async()=>task,reconcileTask:async()=>reconciled,
  });
  assert.equal(result.state,"EvidenceAnchored");
  assert.equal(result.payment.eventIndependentlyChecked,true);
  assert.equal(result.evidence.bytesIndependentlyChecked,true);
  assert.equal(result.retryPolicy.chargeAgain,false);
  assert.equal(result.execution.authority,"ACP");
});
test("maliciously modified evidence cannot alter payment fact",async t=>{
  const {path,evidence}=fixture(t);
  const good=await evidenceHash(evidence);
  writeFileSync(path,JSON.stringify({...evidence,executor:"forged"}));
  const result=await inspectPhase0(req({
    payment:{approval:{},pendingReceipt:{},expectedPayTo:LEDGER},
    evidence:{file:path},
  }),{chain:{},readTask:async()=>({...TASK,evidenceHash:good}),
    reconcileTask:async()=>reconciled});
  assert.equal(result.state,"Conflict");
  assert.equal(result.payment.eventIndependentlyChecked,true);
  assert.equal(result.evidence.fact,"HashMismatch");
});
test("payment reconciliation outage with independently matching bytes is PaymentUnknown",async t=>{
  const {path,evidence}=fixture(t);
  const result=await inspectPhase0(req({
    payment:{approval:{},pendingReceipt:{},expectedPayTo:LEDGER},
    evidence:{file:path},
  }),{chain:{},readTask:async()=>({...TASK,evidenceHash:await evidenceHash(evidence)}),
    reconcileTask:async()=>{throw Error("lost ACK and RPC outage");}});
  assert.equal(result.state,"PaymentUnknown");
  assert.equal(result.inspection.paymentDiagnostic,"PAYMENT_RECONCILIATION_UNAVAILABLE");
  assert.equal(result.evidence.bytesIndependentlyChecked,true);
  assert.equal(result.retryPolicy.chargeAgain,false);
});
test("chain outage never interprets payment attempt as failed",async()=>{
  const result=await inspectPhase0(req(),{
    chain:{},readTask:async()=>{throw Error("RPC timeout");}
  });
  assert.equal(result.state,"PaymentUnknown");
  assert.equal(result.ledgerState,"Unavailable");
  assert.equal(result.inspection.chainDiagnostic,"CHAIN_READ_UNAVAILABLE");
});
test("onchain Paid without receipt is PaymentUnknown even when Recorder attested",async()=>{
  const result=await inspectPhase0(req(),{
    chain:{},readTask:async()=>({...TASK,status:"Paid",evidenceHash:ZERO})
  });
  assert.equal(result.state,"PaymentUnknown");
  assert.equal(result.payment.recorderAttestationObserved,true);
  assert.equal(result.payment.eventIndependentlyChecked,false);
});
test("explicit ledger conflict returns Conflict without second charge",async()=>{
  const result=await inspectPhase0(req({
    payment:{approval:{},pendingReceipt:{},expectedPayTo:LEDGER},
  }),{
    chain:{},readTask:async()=>TASK,
    reconcileTask:async()=>{throw Error("LEDGER_CONFLICT: settlement mismatch");}
  });
  assert.equal(result.state,"Conflict");
  assert.equal(result.retryPolicy.chargeAgain,false);
});

test("untrusted caller cannot claim authoritative ACP NotStarted",async()=>{
  let reads=0;
  await assert.rejects(()=>inspectPhase0(req({attempt:"NotStarted"}),{
    chain:{},readTask:async()=>{reads++;return TASK;}
  }),/requires authenticated ACP-owned context/);
  assert.equal(reads,0);
});
