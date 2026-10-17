#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Hermetic, deterministic Phase-0 visual demo acceptance; NEVER funds Testnet.
import { mkdirSync, writeFileSync } from "node:fs";
import { pathToFileURL, fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { buildDemoCase } from "../public/demo-scenarios.mjs";
import { makeSummary } from "../public/summary.mjs";
import { verifyEvidence } from "../public/evidence-browser.mjs";
import { projectPhase0 } from "../../../phase0/status.mjs";

const insist=(condition,message)=>{if(!condition)throw Error(message)};
const actor="0x"+"75".repeat(20);
const nonce="0x"+"90".repeat(32);
const tx="0x"+"ab".repeat(32);

export async function runDemoJourney() {
  const steps=[];
  for(const scenario of ["created","paid","proven"]) {
    const view=await buildDemoCase(scenario);
    insist(view.kind==="SIMULATION","not a local simulation");
    insist(view.payment.status==="PaymentUnknown" &&
      view.payment.eventIndependentlyChecked===false &&
      view.payment.chargeAgain===false,
      "unsafe payment claim in fixture");
    steps.push({step:scenario,ledgerState:view.task.status,
      paymentObservation:"PaymentUnknown",recorderClaim:view.payment.recorderClaim,
      evidenceVerification:"NOT_RUN",source:"LOCAL_SIMULATION"});
    if(scenario==="proven") {
      const match=await verifyEvidence(view.evidence,view.task,view.inputBytes,view.outputBytes);
      insist(match.match&&match.inputChecked&&match.outputChecked,
        "Evidence input/output digest verification failed");
      steps.push({step:"verify",ledgerState:"Proven",
        paymentObservation:"PaymentUnknown",evidenceVerification:"HASH_MATCHED",
        evidenceHash:match.digest,source:"LOCAL_SIMULATION"});
    }
  }
  const lost=await buildDemoCase("lost-ack");
  insist(lost.task.status==="Paid" && lost.payment.chargeAgain===false &&
    lost.payment.paymentNonce===null,"lost ACK must block repeat charge");
  steps.push({step:"lost-ack",ledgerState:"Paid",
    paymentObservation:"PaymentUnknown",paymentNonce:null,
    failureSemantic:"ACK_LOST_OUTCOME_UNKNOWN",chargeAgain:false,
    source:"LOCAL_SIMULATION"});

  const proven=await buildDemoCase("proven");
  const wrongLedger="0x"+"de".repeat(20);
  const fakeReconciliation={
    status:"READ_ONLY_NO_RECHARGE",taskId:proven.task.taskId,
    ledger:wrongLedger,chainId:10143,
    descriptionHash:proven.task.descriptionHash,payer:actor,
    amountAtomic:proven.task.amountAtomic,
    settlementRef:proven.task.settlementRef,
    paymentNonce:nonce,observedTransaction:tx,
    classification:"PROVEN_PAYMENT_MATCH",
  };
  let crossLedgerRejected=false,wrongIdentityRejected=false;
  try {
    projectPhase0({task:proven.task,attempt:"Reserved",
      reconciliation:fakeReconciliation,evidence:"HashMatched"});
  }catch(error){crossLedgerRejected=/Ledger contract mismatch/.test(error.message);}
  insist(crossLedgerRejected,"cross-Ledger payment proof improperly accepted");
  try {
    projectPhase0({task:proven.task,attempt:"Reserved",
      reconciliation:{...fakeReconciliation,ledger:proven.task.contract,payer:wrongLedger},
      evidence:"HashMatched"});
  }catch(error){wrongIdentityRejected=/payer mismatch/.test(error.message);}
  insist(wrongIdentityRejected,"wrong payer identity improperly accepted");
  steps.push({step:"cross-ledger",result:"REJECTED",chargeAgain:false});
  steps.push({step:"wrong-payer",result:"REJECTED",chargeAgain:false});

  const summary=makeSummary(proven.task,{
    simulation:true,scenario:"created→paid→proven→verify",
    verification:"HASH_MATCHED",
  });
  return {
    ...summary,
    journey:{
      steps,
      createdToRecorderPaidToEvidenceToVerify:"PASS_SIMULATED_ONLY",
      paymentUnknownLostAck:"PASS_NO_RECHARGE",
      crossLedgerRejected,wrongIdentityRejected,
      eventsReal:false,realUSDCSpent:false,
    },
  };
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href){
  runDemoJourney().then(summary=>{
    const path=process.argv[2]?resolve(process.argv[2]):
      fileURLToPath(new URL("../../../out/demo-journey/summary.json",import.meta.url));
    mkdirSync(dirname(path),{recursive:true});
    writeFileSync(path,JSON.stringify(summary,null,2)+"\n",{mode:0o600});
    console.log("SIMULATED JOURNEY PASS / NOT PUBLIC TESTNET ACCEPTANCE: "+path);
  }).catch(error=>{
    console.error("SIMULATED JOURNEY FAILED: "+error.message);
    process.exitCode=1;
  });
}
