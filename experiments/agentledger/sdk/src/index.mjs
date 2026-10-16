// SPDX-License-Identifier: Apache-2.0
// Experimental READ-ONLY verification SDK. No wallet, signer, facilitator,
// recorder write, task execution or ACP control-plane authority.
import { readTask, makeClient, validateLookup, CHAIN_ID, RPC } from "../../examples/verifier/lib/task-reader.mjs";
import { canonicalEvidence, evidenceHash, verifyEvidence } from "../../examples/verifier/public/evidence-browser.mjs";
import { projectPhase0, PHASE0_SCHEMA, STATES } from "../../phase0/status.mjs";
import { verifyLive as verifySettlementLive, verifyReceipt as verifySettlementReceipt } from "../../tools/settlement.mjs";

export const SDK_VERSION = "0.2.0-alpha.1";
export const SDK_CAPABILITIES = Object.freeze({
  readTask: true, verifyEvidence: true, verifyReceipt: true,
  verifySignedPaymentIntent: false, acceptPayment: false, charge: false,
  sign: false, mutateLedger: false, executeAgent: false, manageAcp: false,
  proveChainFinality: false,
});
export { canonicalEvidence, evidenceHash, verifyEvidence, verifySettlementReceipt, PHASE0_SCHEMA, STATES };

/**
 * A deliberately conservative read-only SDK facade.
 * "getStatus" without independently signed approval / precharge evidence
 * can NEVER upgrade recorder Paid/Proven to PaymentRecorded/EvidenceAnchored.
 */
export function createAgentLedgerVerifier({ client, rpcUrl = RPC } = {}) {
  if (client && rpcUrl !== RPC) {
    throw Error("Specify either an injected client or an RPC URL, not both");
  }
  const chain = client || makeClient(rpcUrl);
  return Object.freeze({
    sdkVersion: SDK_VERSION,
    chainId: CHAIN_ID,
    capabilities: SDK_CAPABILITIES,
    async getTask(contract, taskId) {
      validateLookup(contract,taskId);
      return readTask(chain,contract,taskId);
    },
    async getStatus(contract, taskId) {
      const task = await readTask(chain,contract,taskId);
      return projectPhase0({task,attempt:"NotEstablished"});
    },
    async verifyEvidence(contract, taskId, evidence, { inputBytes=null, outputBytes=null }={}) {
      const task=await readTask(chain,contract,taskId);
      const result=await verifyEvidence(evidence,task,inputBytes,outputBytes);
      return {
        ...result,
        sdkVersion:SDK_VERSION,
        evidenceFact:"HashMatchedToOnchainAnchor",
        paymentStatus:"PaymentUnknown",
        paymentVerified:false,
        disclaimer:"Digest equality does not prove payment or Agent execution ownership.",
      };
    },
    async verifySettlement(expected, { minConfirmations=2 }={}) {
      // A valid event is not a signed task intent, a sufficient payment
      // admission, or authorization to call recordPayment.
      if (client) throw Error("Injected RPC clients cannot be used for receipt verification; supply a pinned rpcUrl");
      const result=await verifySettlementLive(expected,rpcUrl,fetch,{minConfirmations});
      return Object.freeze({
        ...result,
        sdkVersion:SDK_VERSION,
        paymentEventObserved:true,
        taskIntentVerified:false,
        finalityProven:false,
        chargeAgain:false,
      });
    },
  });
}
