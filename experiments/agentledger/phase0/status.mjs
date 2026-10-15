// SPDX-License-Identifier: Apache-2.0
/**
 * AgentLedger Phase-0 read-only public projection. NOT an Agent execution FSM.
 * Inputs MUST come from independent live RPC / receipt + SHA-256 verifiers.
 *
 * No charge, signing, retry, task scheduling, executor lease, or recorder write.
 * In particular, PaymentUnknown never means "unpaid" and never permits recharge.
 */
export const PHASE0_SCHEMA = "agentledger.phase0.status.v1";
export const STATES = Object.freeze({
  CREATED: "Created",
  UNKNOWN: "PaymentUnknown",
  OBSERVED: "PaymentObserved",
  RECORDED: "PaymentRecorded",
  ANCHORED: "EvidenceAnchored",
  CONFLICT: "Conflict",
});
const ZERO = "0x" + "0".repeat(64);
const HEX32 = /^0x[0-9a-f]{64}$/;
const ADDRESS = /^0x[0-9a-f]{40}$/;
const check = (yes, reason) => { if (!yes) throw Error(reason); };
const hex32 = (value, name) => {
  check(typeof value === "string" && HEX32.test(value), "invalid " + name);
  return value;
};
const address = (value, name) => {
  check(typeof value === "string" && ADDRESS.test(value.toLowerCase()), "invalid " + name);
  return value.toLowerCase();
};
const uint = (value, name) => {
  check(typeof value === "string" && /^(0|[1-9]\d*)$/.test(value), "invalid " + name);
  return BigInt(value);
};

/**
 * Event fact is only admitted with classification returned by the
 * EIP-712 + USDC Transfer + AuthorizationUsed + precharge reconciler.
 * Projections are NOT proof themselves; consumers must retain raw receipts
 * and verify them separately using live RPC or an independent provider.
 */
export function projectPhase0({task, attempt="NotEstablished", reconciliation=null, evidence="NotSupplied"}={}) {
  check(["NotStarted","Reserved","NotEstablished"].includes(attempt), "invalid attempt state");
  check(["NotSupplied","HashMatched","HashMismatch","Unavailable"].includes(evidence), "invalid evidence check");
  const status=task?.status ?? null;
  if (task !== null && task !== undefined) {
    check(task.chainId===10143, "wrong chain");
    check(["Created","Paid","Proven"].includes(status), "invalid chain task status");
    hex32(task.taskId,"taskId");
    hex32(task.descriptionHash,"descriptionHash");
    hex32(task.settlementRef,"settlementRef");
    hex32(task.evidenceHash,"evidenceHash");
    address(task.payer,"ledger payer");
    uint(task.amountAtomic,"ledger amount");
  }
  const validReconciliation=new Set([
    "SETTLEMENT_OBSERVED_LEDGER_CREATED","PAID_PAYMENT_MATCH","PROVEN_PAYMENT_MATCH"
  ]);
  if (reconciliation!==null) {
    check(task!==null && task!==undefined, "payment reconciliation needs chain state");
    check(validReconciliation.has(reconciliation.classification), "unknown reconciliation class");
    check(reconciliation.taskId===task.taskId, "reconciliation task mismatch");
    hex32(reconciliation.settlementRef,"verified settlement reference");
    hex32(reconciliation.paymentNonce,"verified EIP-3009 nonce");
    hex32(reconciliation.observedTransaction,"verified transaction");
    check(reconciliation.status==="READ_ONLY_NO_RECHARGE", "untrusted reconciliation status");
  }

  let paymentFact="Unverified";
  let paymentStatus=STATES.UNKNOWN;
  let discrepancy=null;
  const chainAttested=status==="Paid"||status==="Proven";
  const paymentEvidence=reconciliation!==null;
  if(reconciliation!==null) {
    const expects={
      Created:"SETTLEMENT_OBSERVED_LEDGER_CREATED",
      Paid:"PAID_PAYMENT_MATCH",
      Proven:"PROVEN_PAYMENT_MATCH",
    };
    if(reconciliation.classification!==expects[status]) {
      paymentStatus=STATES.CONFLICT;
      discrepancy="reconciliation classification differs from on-chain task state";
    } else if(chainAttested &&
      reconciliation.settlementRef!==task.settlementRef) {
      paymentStatus=STATES.CONFLICT;
      discrepancy="on-chain attested settlement reference does not match independently observed transfer";
    } else {
      paymentFact="VerifiedEip3009TransferAndNonce";
      paymentStatus=status==="Created"?STATES.OBSERVED:STATES.RECORDED;
    }
  } else if(status==="Created" && attempt==="NotStarted") {
    // Only ACP or another authoritative actor can assert NotStarted.
    // A missing local journal is NEVER sufficient evidence of NotStarted.
    paymentStatus=STATES.CREATED;
  }
  // Hash equality and payment verification are independent dimensions.
  const anchored=status==="Proven" && task.evidenceHash!==ZERO;
  let evidenceFact=anchored ? "HashAnchoredUnverified":"NoVerifiedAnchor";
  if(evidence==="HashMatched") {
    check(anchored, "cannot mark a non-anchored task hash matched");
    evidenceFact="HashMatchedToOnchainAnchor";
  }
  if(evidence==="HashMismatch") evidenceFact="HashMismatch";
  if(evidence==="Unavailable" && anchored) evidenceFact="HashVerificationUnavailable";
  if(anchored && evidence==="HashMatched" && paymentStatus===STATES.RECORDED) {
    paymentStatus=STATES.ANCHORED;
  }
  if(evidence==="HashMismatch" && anchored) {
    paymentStatus=STATES.CONFLICT;
    discrepancy="local Evidence SHA-256 differs from on-chain anchor";
  }
  // A contract-reported Proven never establishes independent payment.
  // Unknown does not become Paid even if the recorder has written it.
  return {
    schemaVersion:PHASE0_SCHEMA,
    state:paymentStatus,
    ledgerState:status??"Unavailable",
    payment:{
      fact:paymentFact,
      recorderAttestationObserved:chainAttested,
      eventIndependentlyChecked:paymentEvidence && discrepancy===null,
      transaction:paymentEvidence?reconciliation.observedTransaction:null,
      authorizationNonce:paymentEvidence?reconciliation.paymentNonce:null,
      settlementRef:paymentEvidence?reconciliation.settlementRef:null,
    },
    evidence:{
      fact:evidenceFact,
      onchainHash:anchored?task.evidenceHash:null,
      bytesIndependentlyChecked:evidenceFact==="HashMatchedToOnchainAnchor",
    },
    execution:{
      authority:"ACP",
      status:"NOT_REPRESENTED_BY_LEDGER",
      semantics:"Ledger cannot schedule/resume/retry/claim ownership of Agent execution",
    },
    retryPolicy:{
      chargeAgain:false,
      action:paymentStatus===STATES.UNKNOWN||paymentStatus===STATES.CONFLICT ?
        "RECONCILE_READ_ONLY_NO_RECHARGE":"NO_AUTOMATIC_PAYMENT_ACTION",
    },
    discrepancy,
    caveat:"Transfer + AuthorizationUsed proves observed token events, not finality or delivery. SHA-256 match proves bytes, not independent Agent correctness.",
  };
}
