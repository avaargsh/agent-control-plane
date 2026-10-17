// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { PHASE0_SCHEMA, STATES, projectPhase0 } from "../phase0/status.mjs";

const ZERO="0x"+"0".repeat(64);
const ID="0x"+"11".repeat(32);
const LEDGER="0x"+"a1".repeat(20);
const DESCRIPTION="0x"+"22".repeat(32);
const HASH="0x"+"33".repeat(32);
const REF="0x"+"44".repeat(32);
const PAYER="0x"+"55".repeat(20);
const task=(state="Created")=>({
  chainId:10143,contract:LEDGER,taskId:ID,descriptionHash:DESCRIPTION,
  status:state,payer:state==="Created"?"0x"+"0".repeat(40):PAYER,
  amountAtomic:state==="Created"?"0":"1000",
  settlementRef:state==="Created"?ZERO:REF,
  evidenceHash:state==="Proven"?HASH:ZERO,
});
const reconcile=(state="Created")=>({
  status:"READ_ONLY_NO_RECHARGE",taskId:ID,ledger:LEDGER,chainId:10143,
  descriptionHash:DESCRIPTION,payer:PAYER,amountAtomic:"1000",settlementRef:REF,
  classification:{Created:"SETTLEMENT_OBSERVED_LEDGER_CREATED",
    Paid:"PAID_PAYMENT_MATCH",Proven:"PROVEN_PAYMENT_MATCH"}[state],
  paymentNonce:"0x"+"66".repeat(32),
  observedTransaction:"0x"+"77".repeat(32)
});
test("Created requires authoritative NotStarted; missing receipt is PaymentUnknown",()=>{
  const created=projectPhase0({task:task(),attempt:"NotStarted"});
  assert.equal(created.schemaVersion,PHASE0_SCHEMA);
  assert.equal(created.state,STATES.CREATED);
  assert.equal(created.retryPolicy.chargeAgain,false);
  for(const attempt of ["Reserved","NotEstablished"]){
    const unknown=projectPhase0({task:task(),attempt});
    assert.equal(unknown.state,STATES.UNKNOWN);
    assert.equal(unknown.retryPolicy.action,"RECONCILE_READ_ONLY_NO_RECHARGE");
  }
});
test("PaymentUnknown is sticky even with chain recorder Paid/Proven claims",()=>{
  for(const state of ["Paid","Proven"]){
    const projected=projectPhase0({task:task(state),attempt:"Reserved"});
    assert.equal(projected.state,STATES.UNKNOWN);
    assert.equal(projected.payment.recorderAttestationObserved,true);
    assert.equal(projected.payment.eventIndependentlyChecked,false);
    assert.equal(projected.execution.authority,"UNVERIFIED_EXTERNAL");
    assert.equal(projected.retryPolicy.chargeAgain,false);
  }
});
test("verified USDC event is separate from onchain recorder attestation",()=>{
  const observed=projectPhase0({task:task(),attempt:"Reserved",reconciliation:reconcile()});
  assert.equal(observed.state,STATES.OBSERVED);
  assert.equal(observed.payment.fact,"VerifiedEip3009TransferAndNonce");
  assert.equal(observed.payment.recorderAttestationObserved,false);
  const paid=projectPhase0({task:task("Paid"),attempt:"Reserved",reconciliation:reconcile("Paid")});
  assert.equal(paid.state,STATES.RECORDED);
  assert.equal(paid.payment.recorderAttestationObserved,true);
  assert.equal(paid.evidence.fact,"NoVerifiedAnchor");
});
test("EvidenceAnchored only once independent payment and hash facts both match",()=>{
  const proper=projectPhase0({task:task("Proven"),attempt:"Reserved",
    reconciliation:reconcile("Proven"),evidence:"HashMatched"});
  assert.equal(proper.state,STATES.ANCHORED);
  assert.equal(proper.payment.eventIndependentlyChecked,true);
  assert.equal(proper.evidence.bytesIndependentlyChecked,true);
  const noPayment=projectPhase0({task:task("Proven"),attempt:"Reserved",evidence:"HashMatched"});
  assert.equal(noPayment.state,STATES.UNKNOWN);
  assert.equal(noPayment.evidence.fact,"HashMatchedToOnchainAnchor");
  const noBytes=projectPhase0({task:task("Proven"),attempt:"Reserved",
    reconciliation:reconcile("Proven"),evidence:"NotSupplied"});
  assert.equal(noBytes.state,STATES.RECORDED);
  assert.equal(noBytes.evidence.fact,"HashAnchoredUnverified");
});
test("conflicting receipt and changed evidence can never promote Proven",()=>{
  const r={...reconcile("Paid"),settlementRef:HASH};
  const conflict=projectPhase0({task:task("Paid"),reconciliation:r});
  assert.equal(conflict.state,STATES.CONFLICT);
  assert.equal(conflict.retryPolicy.chargeAgain,false);
  const modified=projectPhase0({task:task("Proven"),reconciliation:reconcile("Proven"),
    evidence:"HashMismatch"});
  assert.equal(modified.state,STATES.CONFLICT);
  assert.equal(modified.payment.fact,"VerifiedEip3009TransferAndNonce");
  assert.equal(modified.payment.eventIndependentlyChecked,true);
  assert.equal(modified.evidence.fact,"HashMismatch");
});
test("missing chain or RPC does not assert Created or payment failure",()=>{
  const v=projectPhase0({task:null,attempt:"Reserved",evidence:"Unavailable"});
  assert.equal(v.state,STATES.UNKNOWN);
  assert.equal(v.ledgerState,"Unavailable");
  assert.equal(v.retryPolicy.chargeAgain,false);
});
test("fail closed on fabricated reconciliation or hash match without anchor",()=>{
  assert.throws(()=>projectPhase0({task:task(),reconciliation:{...reconcile(),taskId:HASH}}),/task mismatch/);
  assert.throws(()=>projectPhase0({task:task(),evidence:"HashMatched"}),/cannot mark/);
  assert.throws(()=>projectPhase0({task:{...task(),chainId:1}}),/wrong chain/);
  assert.throws(()=>projectPhase0({task:task(),attempt:"Failed"}),/attempt/);
});

test("a matching taskId and settlementRef on a DIFFERENT Ledger cannot authorize payment fact",()=>{
  const stolen={...reconcile("Proven"),ledger:"0x"+"b2".repeat(20)};
  assert.throws(()=>projectPhase0({
    task:task("Proven"),attempt:"Reserved",reconciliation:stolen,evidence:"HashMatched"
  }),/Ledger contract mismatch/);
  assert.throws(()=>projectPhase0({
    task:task("Proven"),attempt:"Reserved",
    reconciliation:{...reconcile("Proven"),chainId:1},evidence:"HashMatched"
  }),/chain mismatch/);
  assert.throws(()=>projectPhase0({
    task:task("Proven"),attempt:"Reserved",
    reconciliation:{...reconcile("Proven"),descriptionHash:HASH},evidence:"HashMatched"
  }),/task description mismatch/);
});
test("valid settlement from same Ledger still requires matching payer and atomic amount",()=>{
  for(const bad of [{payer:"0x"+"cd".repeat(20)},{amountAtomic:"1001"}]){
    assert.throws(()=>projectPhase0({
      task:task("Paid"),reconciliation:{...reconcile("Paid"),...bad}
    }),/reconciliation (payer|amount) mismatch/);
  }
});
