// SPDX-License-Identifier: Apache-2.0
// Read-only Phase 0 adapter. Consumes existing verification APIs instead of
// creating another Task/Agent controller or a second payment write path.
import { readFileSync, statSync } from "node:fs";
import { makeClient, readTask } from "./task-reader.mjs";
import { reconcileTask } from "./reconcile-task.mjs";
import { verifyEvidence } from "../public/evidence-browser.mjs";
import { projectPhase0 } from "../../../phase0/status.mjs";

function readLocal(path, maxSize) {
  if (typeof path !== "string" || !path) throw Error("invalid local input path");
  if (statSync(path).size > maxSize) throw Error("verification file exceeds size bound");
  return readFileSync(path);
}

/**
 * Sources are separately verifiable:
 * 1. Existing payer EIP-712 + on-chain USDC events + precharge reconciliation.
 * 2. Existing Evidence JSON + raw bytes SHA-256 vs onchain evidenceHash.
 *
 * A failed verification never implies a payment failed or can be retried.
 * A missing evidence file does NOT prove a missing onchain hash anchor.
 */
export async function inspectPhase0(request, deps={}) {
  if (request?.schemaVersion !== "agentledger.phase0.inspect.v1") {
    throw Error("unsupported Phase 0 inspection request");
  }
  const {ledger,taskId}=request;
  if (!/^0x[0-9a-fA-F]{40}$/.test(ledger||"") ||
      !/^0x[0-9a-fA-F]{64}$/.test(taskId||"")) {
    throw Error("invalid ledger or taskId");
  }
  const attempt = request.attempt || "NotEstablished";
  // Read-only CLI/client JSON is NOT an authenticated execution-owner assertion. A caller
  // cannot manufacture NotStarted by selecting a string in the request.
  if (attempt === "NotStarted" && deps.acpAuthoritativeNotStarted !== true) {
    throw Error("NotStarted requires authenticated execution-owner context");
  }
  let task=null, chainError=null, paymentError=null, evidenceError=null;
  let reconciliation=null, evidence="NotSupplied";
  try {
    const client=deps.chain || makeClient(process.env.MONAD_RPC_URL);
    task=await (deps.readTask || readTask)(client,ledger,taskId);
  } catch (error) {
    chainError="CHAIN_READ_UNAVAILABLE";
  }
  // A read obtained for another contract or another task must NEVER be
  // combined with the signed payment context supplied for this inspection.
  if(task && (task.chainId !== 10143 ||
      task.contract?.toLowerCase() !== ledger.toLowerCase() ||
      task.taskId?.toLowerCase() !== taskId.toLowerCase())) {
    task=null;
    chainError="CHAIN_CONTEXT_MISMATCH";
  }
  if(task) {
    const payment=request.payment;
    if(payment) {
      try {
        if(!payment.approval || !payment.pendingReceipt || !payment.expectedPayTo) {
          throw Error("signed quote/receipt/payee required");
        }
        if(payment.approval.ledger?.toLowerCase()!==ledger.toLowerCase() ||
           payment.approval.taskId?.toLowerCase()!==taskId.toLowerCase()) {
          throw Error("PAYMENT_CONTEXT_MISMATCH");
        }
        const candidate=await (deps.reconcileTask || reconcileTask)({
          approval:payment.approval,
          pendingReceipt:payment.pendingReceipt,
          expectedPayTo:payment.expectedPayTo,
        },deps);
        if(candidate?.ledger?.toLowerCase()!==ledger.toLowerCase() ||
           candidate?.taskId?.toLowerCase()!==taskId.toLowerCase() ||
           candidate?.chainId!==10143 ||
           candidate?.descriptionHash!==task.descriptionHash) {
          throw Error("PAYMENT_CONTEXT_MISMATCH");
        }
        reconciliation=candidate;
      } catch (error) {
        // Diagnostics deliberately do not expose RPC URLs, payloads or keys.
        const errorMessage=String(error.message);
        paymentError=errorMessage.includes("PAYMENT_CONTEXT_MISMATCH") ?
          "PAYMENT_CONTEXT_MISMATCH" :
          errorMessage.includes("LEDGER_CONFLICT") ?
            "LEDGER_PAYMENT_CONFLICT":"PAYMENT_RECONCILIATION_UNAVAILABLE";
      }
    }
    if(request.evidence) {
      try {
        const cfg=request.evidence;
        const document=JSON.parse(readLocal(cfg.file,2*1024*1024).toString("utf8"));
        const input=cfg.inputFile ? readLocal(cfg.inputFile,10*1024*1024):null;
        const output=cfg.outputFile ? readLocal(cfg.outputFile,10*1024*1024):null;
        await (deps.verifyEvidence || verifyEvidence)(document,task,input,output);
        evidence="HashMatched";
      } catch(error) {
        const msg=String(error.message);
        evidence=/(SHA-256 mismatch|bytes SHA-256 mismatch|task ID does not match)/.test(msg) ?
          "HashMismatch":"Unavailable";
        evidenceError=evidence==="HashMismatch" ?
          "EVIDENCE_BYTES_MISMATCH":"EVIDENCE_VERIFICATION_UNAVAILABLE";
      }
    }
  } else if(request.evidence) {
    evidence="Unavailable";
    evidenceError="CHAIN_UNAVAILABLE_CANNOT_VERIFY_EVIDENCE";
  }
  // Reconciliation disagreement is fail-closed; nothing may report success.
  // If payment is verifiably contradictory, expose Conflict rather than
  // interpreting it as a failed/retryable charge.
  const projected=projectPhase0({task,attempt,reconciliation,evidence});
  const conflict=paymentError==="LEDGER_PAYMENT_CONFLICT" ||
    paymentError==="PAYMENT_CONTEXT_MISMATCH";
  return {
    ...projected,
    ...(conflict?{state:"Conflict",discrepancy:"payment evidence conflicts with the recorder's onchain claim"}:{}),
    inspection:{
      mode:"READ_ONLY",
      paymentDiagnostic:paymentError,
      evidenceDiagnostic:evidenceError,
      chainDiagnostic:chainError,
      paymentEventCheckedByExistingReconciler: Boolean(reconciliation),
      paymentWitness: reconciliation ? {
        transaction: reconciliation.observedTransaction,
        blockNumber: reconciliation.blockNumber,
        transactionIndex: reconciliation.transactionIndex,
        blockHash: reconciliation.blockHash,
        canonicalBlockCheckedAtObservation: reconciliation.canonicalBlockCheckedAtObservation,
        observedHeadBlockNumber: reconciliation.observedHeadBlockNumber,
        confirmations: reconciliation.confirmations,
      } : null,
      sourceClaims:["verified-USDC-Transfer-plus-AuthorizationUsed",
        "independent-SHA256-of-evidence-and-optional-original-bytes"],
    },
  };
}
