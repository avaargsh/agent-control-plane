// SPDX-License-Identifier: Apache-2.0
// Safe, source-labelled export contract. Only observations are serialized;
// this cannot certify a payment, public deployment, or execution ownership.
const HASH=/^0x[0-9a-fA-F]{64}$/;
const ADDRESS=/^0x[0-9a-fA-F]{40}$/;
export function serializeSummaryJson(report:unknown):string{
  if(!report||typeof report!=="object"||Array.isArray(report))
    throw Error("SUMMARY_INVALID");
  const r=report as Record<string,any>;
  if(!["LOCAL_SIMULATION","TESTNET_TASK_READ_ONLY"].includes(r.mode) ||
     r.schemaVersion!=="agentledger.pocket.summary.v1" ||
     r.outcome!==(r.mode==="LOCAL_SIMULATION"?
       "SIMULATED_ONLY_NOT_TESTNET_ACCEPTANCE":
       "TASK_OBSERVED_NOT_PAYMENT_ACCEPTANCE") ||
     r.chainId!==10143 || typeof r.contract!=="string" ||
     !ADDRESS.test(r.contract) ||
     !HASH.test(r.task?.taskId??"") ||
     !HASH.test(r.task?.descriptionHash??"") ||
     r.payment?.observed!=="PaymentUnknown" ||
     !["NOT_VERIFIED","NOT_INDEPENDENTLY_VERIFIED"].includes(r.payment?.transferEvent) ||
     !["NOT_VERIFIED","NOT_INDEPENDENTLY_VERIFIED"].includes(r.payment?.authorizationUsed) ||
     r.payment?.chargeAgain!==false ||
     r.payment?.nonce!==null ||
     r.security?.publicTestnetAcceptance!=="BLOCKED_NO_REVIEWED_DEPLOYMENT" ||
     r.security?.reviewedDeployment!==false ||
     r.security?.mainnetAccepted!==false ||
     r.security?.realFundsUsed!==false ||
     r.security?.executionAuthority!=="ACP" ||
     r.rpc?.contacted!==(r.mode==="TESTNET_TASK_READ_ONLY")) {
    throw Error("SUMMARY_PROVENANCE_NOT_VERIFIED");
  }
  const text=JSON.stringify(r,null,2)+"\n";
  if(text.length>256*1024)throw Error("SUMMARY_TOO_LARGE");
  return text;
}
