// SPDX-License-Identifier: Apache-2.0
// Private v0.2 alpha.2: three READ-ONLY public operations. No caller-provided
// client/transport or fixture parser is part of the public package surface.
import { readTask, makeClient, CHAIN_ID, RPC } from "../../examples/verifier/lib/task-reader.mjs";
import { verifyEvidence } from "../../examples/verifier/public/evidence-browser.mjs";
import { projectPhase0 } from "../../phase0/status.mjs";
import { verifyLive } from "../../tools/settlement.mjs";

export const SDK_VERSION="0.2.0-alpha.2";
export const SDK_CAPABILITIES=Object.freeze({
  observe:true,verify:true,reconcile:true,readOnly:true,
  acceptPayment:false,charge:false,sign:false,mutateLedger:false,
  executeAgent:false,manageAcp:false,proveChainFinality:false,
});
const NONCE=/^0x[0-9a-fA-F]{64}$/;
const denyTransport="Injected transport/client is forbidden in public SDK; use rpcUrl. No receipt or charge attempted";

/** Public verification facade. rpcUrl is configured once, never per call.
 * Caller-provided client/transport objects are rejected at construction,
 * BEFORE validation of payment nonces or any RPC action.
 */
export function createAgentLedgerVerifier(options={}) {
  if(!options || typeof options!=="object" || Array.isArray(options))throw Error("SDK options must be an object");
  if(Object.keys(options).some(k=>k==="client"||k==="transport"||k==="fetch"||k==="request")) {
    throw Error(denyTransport);
  }
  if(Object.keys(options).some(k=>k!=="rpcUrl"))throw Error("Unknown SDK option");
  const rpcUrl=options.rpcUrl??RPC;
  // Validate endpoint at construction, but create no transaction nor RPC call.
  const chain=makeClient(rpcUrl);
  return Object.freeze({
    sdkVersion:SDK_VERSION,
    capabilities:SDK_CAPABILITIES,
    async observe(contract,taskId) {
      const task=await readTask(chain,contract,taskId);
      return Object.freeze({
        sdkVersion:SDK_VERSION,source:"RPC_READ_ONLY",task,
        status:projectPhase0({task,attempt:"NotEstablished"}),
        publicTestnetAcceptance:"BLOCKED_UNLESS_SEPARATELY_REVIEWED",
        finalityProven:false,chargeAgain:false,executionAuthority:"ACP",
      });
    },
    async verify(contract,taskId,evidence,{inputBytes=null,outputBytes=null}={}) {
      const task=await readTask(chain,contract,taskId);
      const matched=await verifyEvidence(evidence,task,inputBytes,outputBytes);
      return Object.freeze({
        ...matched,sdkVersion:SDK_VERSION,
        source:"CHAIN_ANCHOR_PLUS_LOCAL_BYTES",evidenceHash:task.evidenceHash,
        paymentStatus:"PaymentUnknown",paymentVerified:false,
        executionAuthority:"ACP",
        publicTestnetAcceptance:"BLOCKED_UNLESS_SEPARATELY_REVIEWED",
      });
    },
    async reconcile(expected,{minConfirmations=2}={}) {
      // This is a narrow read-only receipt observation, NOT a full EIP-712
      // payer intent + precharge proof. The public status remains unknown.
      // Never expose a `Paid` result from an unbound receipt alone.
      if(!NONCE.test(expected?.paymentNonce||"")) {
        throw Error("EIP-3009 paymentNonce required; PaymentUnknown, do not recharge");
      }
      const payment=await verifyLive(expected,rpcUrl,fetch,{minConfirmations});
      return Object.freeze({
        sdkVersion:SDK_VERSION,source:"USDC_RECEIPT_ONLY",
        paymentEventObserved:true,nonce:payment.paymentNonce,
        transaction:payment.transaction,blockHash:payment.blockHash,
        settlementRef:payment.settlementRef,chainId:CHAIN_ID,
        status:"PaymentUnknown",payerIntentVerified:false,
        prechargeReservationVerified:false,finalityProven:false,
        publicTestnetAcceptance:"BLOCKED_UNLESS_SEPARATELY_REVIEWED",
        chargeAgain:false,executionAuthority:"ACP",
        note:"Read-only Transfer + AuthorizationUsed observation. Full signed intent and durable reservation reconciliation stays in Phase0 CLI.",
      });
    },
  });
}
