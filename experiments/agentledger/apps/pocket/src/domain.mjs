// SPDX-License-Identifier: Apache-2.0
// Mobile-first observer: record facts and uncertainty, NEVER claim paid from
// a Recorder enum alone. This module has no transport and no wallet methods.
import { DEMO_DIGEST } from "./evidence.mjs";
export const CHAIN_ID=10143;
export const RPC_ID="https://testnet-rpc.monad.xyz/";
export const HEX32=/^0x[0-9a-fA-F]{64}$/;
export const ADDRESS=/^0x[0-9a-fA-F]{40}$/;
const TASK_ID="0x"+"23".repeat(32);
const LEDGER="0x"+"42".repeat(20);
const PAYER="0x"+"75".repeat(20);
const CREATOR="0x"+"64".repeat(20);
const DESC="0x"+"a1".repeat(32);
const REF="0x"+"8a".repeat(32);
const ZERO="0x"+"00".repeat(32);

export const SCENARIOS=Object.freeze([
  {key:"created",name:"Created",summary:"任务已创建",hint:"No payment attempted",color:"blue"},
  {key:"paid",name:"Recorder Paid",summary:"登记支付声明",hint:"Not independently settled",color:"gold"},
  {key:"proven",name:"Evidence Anchored",summary:"证据哈希已上链",hint:"Hash verification required",color:"green"},
  {key:"unknown",name:"PaymentUnknown",summary:"丢失支付 ACK",hint:"Never charge again",color:"red"},
]);
export function demoTask(stage="created"){
  if(!SCENARIOS.some(s=>s.key===stage))throw Error("Unknown demo stage");
  const status=stage==="created"?"Created":stage==="proven"?"Proven":"Paid";
  return Object.freeze({
    chainId:CHAIN_ID,contract:LEDGER,taskId:TASK_ID,
    descriptionHash:DESC,creator:CREATOR,
    payer:status==="Created"?"0x"+"00".repeat(20):PAYER,
    amountAtomic:status==="Created"?"0":"1000",
    maxBudgetAtomic:"2000",settlementRef:status==="Created"?ZERO:REF,
    evidenceHash:stage==="proven"?DEMO_DIGEST:ZERO,
    status,stage,source:"LOCAL_SIMULATION",
  });
}
export function assertTask(task,expected={}) {
  if(!task || task.chainId!==CHAIN_ID || !ADDRESS.test(task.contract||"") ||
     !HEX32.test(task.taskId||"") || !HEX32.test(task.descriptionHash||"") ||
     !HEX32.test(task.evidenceHash||"") || !HEX32.test(task.settlementRef||"") ||
     !["Created","Paid","Proven"].includes(task.status) ||
     !/^(0|[1-9][0-9]*)$/.test(task.amountAtomic||"")){
    throw Error("TASK_IDENTITY_OR_FIELDS_INVALID");
  }
  if(expected.contract && task.contract.toLowerCase()!==expected.contract.toLowerCase()){
    throw Error("CROSS_LEDGER_REJECTED");
  }
  if(expected.taskId && task.taskId.toLowerCase()!==expected.taskId.toLowerCase()){
    throw Error("TASK_IDENTITY_MISMATCH");
  }
  return task;
}
export function assertPaymentIdentity(task,proof) {
  assertTask(task);
  if(proof.ledger?.toLowerCase()!==task.contract.toLowerCase())throw Error("CROSS_LEDGER_REJECTED");
  if(proof.taskId?.toLowerCase()!==task.taskId.toLowerCase())throw Error("TASK_IDENTITY_MISMATCH");
  if(proof.chainId!==CHAIN_ID)throw Error("WRONG_CHAIN_REJECTED");
  if(proof.payer?.toLowerCase()!==task.payer.toLowerCase())throw Error("PAYER_IDENTITY_REJECTED");
  if(proof.descriptionHash?.toLowerCase()!==task.descriptionHash.toLowerCase())throw Error("DESCRIPTION_MISMATCH");
  return true; // still NOT payment proof; nonce + actual USDC receipt needed.
}
export function publicObservation(task,{simulation=false,evidenceChecked=false}={}) {
  assertTask(task);
  return Object.freeze({
    chainId:CHAIN_ID,contract:task.contract,taskId:task.taskId,
    descriptionHash:task.descriptionHash,
    source:simulation?"LOCAL_SIMULATION":"TESTNET_TASK_READ_ONLY",
    recorderClaim:task.status==="Created"?"NotRecorded":"Paid",
    ledgerState:task.status,
    paymentStatus:"PaymentUnknown",
    paymentEventVerified:false,
    authorizationUsedVerified:false,paymentNonce:null,
    chargeAgain:false,
    evidenceHash:task.evidenceHash,
    evidenceState:task.status==="Proven"?
      evidenceChecked?"DIGEST_MATCHED_ONLY":"ANCHOR_OBSERVED_BYTES_NOT_CHECKED":
      "NOT_ANCHORED",
    executionAuthority:"ACP",executionCorrectnessProven:false,
    pinnedRpcIdentifier:RPC_ID,rpcContacted:!simulation,
    reviewedDeployment:false,
    publicTestnetAcceptance:"BLOCKED_NO_REVIEWED_DEPLOYMENT",
    finalityProven:false,
  });
}
export function demoIdentityRejections(){
  const t=demoTask("proven");
  const valid={ledger:t.contract,taskId:t.taskId,chainId:CHAIN_ID,
    payer:t.payer,descriptionHash:t.descriptionHash};
  const rejected=[];
  for(const [kind,value] of [
    ["CROSS_LEDGER",{ledger:"0x"+"dd".repeat(20)}],
    ["WRONG_PAYER",{payer:"0x"+"ee".repeat(20)}],
  ]) {
    try{assertPaymentIdentity(t,{...valid,...value});}
    catch(error){rejected.push({kind,result:error.message});}
  }
  return rejected;
}
export function summaryJson(task,{simulation=false,evidenceChecked=false}={}) {
  return JSON.stringify({
    schemaVersion:"agentledger.pocket.summary.v1",
    ...publicObservation(task,{simulation,evidenceChecked}),
    warning:"No wallet, USDC transfer or execution correctness was verified.",
  },null,2);
}
