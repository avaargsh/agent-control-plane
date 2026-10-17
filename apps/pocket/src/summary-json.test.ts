// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import {serializeSummaryJson} from "./summary-json";
import {FACTS,exportSummary} from "./facts";
import {taskReadSummary,TaskRead} from "./observe";
const sim=exportSummary(FACTS.evidence,true);
test("summary.json local simulation remains a non-payment acceptance observation",()=>{
  const report=JSON.parse(serializeSummaryJson(sim));
  assert.equal(report.mode,"LOCAL_SIMULATION");
  assert.equal(report.rpc.contacted,false);
  assert.equal(report.payment.observed,"PaymentUnknown");
  assert.equal(report.payment.chargeAgain,false);
  assert.equal(report.security.publicTestnetAcceptance,"BLOCKED_NO_REVIEWED_DEPLOYMENT");
  assert.equal(report.evidence.verification,"SIMULATED_HASH_MATCHED_TO_PINNED_REFERENCE");
});
test("task read summary never upgrades Recorder Paid to verified settlement",()=>{
  const task:TaskRead={
    chainId:10143,contract:"0x"+"42".repeat(20),taskId:"0x"+"11".repeat(32),
    descriptionHash:"0x"+"12".repeat(32),evidenceHash:"0x"+"13".repeat(32),
    settlementRef:"0x"+"14".repeat(32),payer:"0x"+"22".repeat(20),
    creator:"0x"+"33".repeat(20),amountAtomic:"1000",maxBudgetAtomic:"1000",
    status:"Proven",
  };
  const report=JSON.parse(serializeSummaryJson(taskReadSummary(task,"HASH_MATCHED_EVIDENCE_ONLY")));
  assert.equal(report.mode,"TESTNET_TASK_READ_ONLY");
  assert.equal(report.rpc.contacted,true);
  assert.equal(report.payment.observed,"PaymentUnknown");
  assert.equal(report.security.reviewedDeployment,false);
});
test("malformed, paid or provenance-spoofed reports are refused",()=>{
  for(const changed of [
    {payment:{...sim.payment,observed:"Paid"}},
    {payment:{...sim.payment,chargeAgain:true}},
    {payment:{...sim.payment,nonce:"0x"+"ee".repeat(32)}},
    {security:{...sim.security,publicTestnetAcceptance:"PASS"}},
    {security:{...sim.security,reviewedDeployment:true}},
    {payment:{...sim.payment,transferEvent:"VERIFIED"}},
    {payment:{...sim.payment,authorizationUsed:"VERIFIED"}},
    {outcome:"MAINNET_ACCEPTED"},
    {schemaVersion:"forged.v2"},
    {security:{...sim.security,mainnetAccepted:true}},
    {rpc:{...sim.rpc,contacted:true}},
    {task:{...sim.task,taskId:"INVALID"}},
  ]){
    assert.throws(()=>serializeSummaryJson({...sim,...changed}),/PROVENANCE_NOT_VERIFIED/);
  }
});
