// SPDX-License-Identifier: Apache-2.0
// Common read-only report contract for the browser Studio and hermetic CLI.
// A simulated snapshot can NEVER represent a real Testnet payment.
export const SUMMARY_SCHEMA="agentledger.demo.summary.v1";
export const RPC_LABEL="https://testnet-rpc.monad.xyz/"; // identity only, not contacted for simulations
export function makeSummary(task,{simulation=true,payment=null,verification="NOT_RUN",scenario=null}={}) {
  if(!task || typeof task!=="object" || !/^0x[0-9a-fA-F]{64}$/.test(task.taskId||"") ||
    !/^0x[0-9a-fA-F]{64}$/.test(task.descriptionHash||"") ||
    !/^0x[0-9a-fA-F]{40}$/.test(task.contract||"") || task.chainId!==10143) {
    throw Error("Cannot summarize a mismatched/unknown task identity");
  }
  const proof=payment||{};
  // Never turn a Recorder claim into independent USDC settlement.
  if(proof.eventIndependentlyChecked===true) {
    throw Error("Studio cannot certify independently checked payment from a simulation or bare task read");
  }
  return {
    schemaVersion:SUMMARY_SCHEMA,mode:simulation?"LOCAL_SIMULATION":"TESTNET_TASK_READ_ONLY",
    outcome:simulation?"SIMULATED_OBSERVATION_NOT_PUBLIC_ACCEPTANCE":"TASK_ONLY_NOT_SETTLEMENT_ACCEPTANCE",
    scenario,chainId:10143,contract:task.contract.toLowerCase(),
    rpc:{
      pinnedIdentifier:RPC_LABEL,
      contacted:!simulation,
      note:simulation?"Identity label only; simulation makes no RPC call":"Read-only task request; payment receipts not independently checked",
    },
    task:{
      taskId:task.taskId.toLowerCase(),
      descriptionHash:task.descriptionHash.toLowerCase(),
      ledgerState:task.status,
      payer:task.payer,
      amountAtomic:task.amountAtomic,
      settlementRef:task.settlementRef,
    },
    payment:{
      observedState:"PaymentUnknown",
      recorderClaim:task.status==="Created"?"NotRecorded":"Paid",
      transferEvent:"NOT_INDEPENDENTLY_VERIFIED",
      authorizationUsed:"NOT_INDEPENDENTLY_VERIFIED",
      paymentNonce:null,
      transaction:null,
      settlementRef:task.status==="Created"?null:task.settlementRef,
      chargeAgain:false,
      failureSemantic:scenario==="lost-ack"?"ACK_LOST_OUTCOME_UNKNOWN":"RECORDER_STATE_IS_NOT_USDC_PROOF",
    },
    evidence:{
      evidenceHash:task.evidenceHash,
      verification,
      bytesIndependentlyChecked:verification==="HASH_MATCHED",
      businessExecutionCorrectnessVerified:false,
    },
    boundaries:{
      executionAuthority:"ACP",
      reviewedDeployment:false,
      publicTestnetAcceptance:"BLOCKED_NO_REVIEWED_DEPLOYMENT",
      finalityProven:false,
      isMainnetAcceptance:false,
      isTestnetPaymentAcceptance:false,
      walletOperationPerformed:false,
    },
  };
}
