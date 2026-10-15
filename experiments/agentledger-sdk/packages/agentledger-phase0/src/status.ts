// SPDX-License-Identifier: Apache-2.0
/**
 * AgentLedger Phase-0 read-only public projection. NOT an Agent execution FSM.
 * Inputs MUST come from independent live RPC / receipt + SHA-256 verifiers.
 *
 * No charge, signing, retry, task scheduling, executor lease, or recorder write.
 * In particular, PaymentUnknown never means "unpaid" and never permits recharge.
 */
const PHASE0_SCHEMA = "agentledger.phase0.status.v1";
const STATES = Object.freeze({
  CREATED: "Created",
  UNKNOWN: "PaymentUnknown",
  OBSERVED: "PaymentObserved",
  RECORDED: "PaymentRecorded",
  ANCHORED: "EvidenceAnchored",
  CONFLICT: "Conflict",
});
export type TaskStatus = (typeof STATES)[keyof typeof STATES];

const ZERO = "0x" + "0".repeat(64);
const HEX32 = /^0x[0-9a-f]{64}$/;
const ADDRESS = /^0x[0-9a-f]{40}$/;
const check = (yes: boolean, reason: string): void => { if (!yes) throw Error(reason); };
const hex32 = (value: unknown, name: string): string => {
  check(typeof value === "string" && HEX32.test(value), "invalid " + name);
  return value as string;
};
const address = (value: unknown, name: string): string => {
  check(typeof value === "string" && ADDRESS.test(value.toLowerCase()), "invalid " + name);
  return (value as string).toLowerCase();
};
const uint = (value: unknown, name: string): bigint => {
  check(typeof value === "string" && /^(0|[1-9]\d*)$/.test(value), "invalid " + name);
  return BigInt(value as string);
};

export interface ChainTask {
  chainId: number;
  status: "Created" | "Paid" | "Proven";
  taskId: string;
  descriptionHash: string;
  settlementRef: string;
  evidenceHash: string;
  payer: string;
  amountAtomic: bigint;
  contract: string;
}

export interface ReconciliationInput {
  classification: "SETTLEMENT_OBSERVED_LEDGER_CREATED" | "PAID_PAYMENT_MATCH" | "PROVEN_PAYMENT_MATCH";
  chainId: number;
  taskId: string;
  ledger: string;
  payer: string;
  amountAtomic: string;
  descriptionHash: string;
  settlementRef: string;
  paymentNonce: string;
  observedTransaction: string;
  status: "READ_ONLY_NO_RECHARGE";
}

export interface ProjectPhase0Input {
  task: ChainTask | null;
  attempt?: "NotStarted" | "Reserved" | "NotEstablished";
  reconciliation: ReconciliationInput | null;
  evidence: "NotSupplied" | "HashMatched" | "HashMismatch" | "Unavailable";
}

export interface ProjectPhase0Output {
  schemaVersion: typeof PHASE0_SCHEMA;
  state: TaskStatus;
  ledgerState: string;
  payment: {
    fact: string;
    recorderAttestationObserved: boolean;
    eventIndependentlyChecked: boolean;
    transaction: string | null;
    authorizationNonce: string | null;
    settlementRef: string | null;
  };
  evidence: {
    fact: string;
    onchainHash: string | null;
    bytesIndependentlyChecked: boolean;
  };
  execution: {
    authority: string;
    status: string;
    semantics: string;
  };
  retryPolicy: {
    chargeAgain: boolean;
    action: string;
  };
  discrepancy: string | null;
  caveat: string;
}

function projectPhase0(input: ProjectPhase0Input): ProjectPhase0Output {
  const { task, attempt = "NotEstablished", reconciliation = null, evidence = "NotSupplied" } = input;
  check(["NotStarted", "Reserved", "NotEstablished"].includes(attempt), "invalid attempt state");
  check(["NotSupplied", "HashMatched", "HashMismatch", "Unavailable"].includes(evidence), "invalid evidence check");
  const status = task?.status ?? null;
  if (task !== null && task !== undefined) {
    check(task.chainId === 10143, "wrong chain");
    check(status !== null && ["Created", "Paid", "Proven"].includes(status), "invalid chain task status");
    hex32(task.taskId, "taskId");
    hex32(task.descriptionHash, "descriptionHash");
    hex32(task.settlementRef, "settlementRef");
    hex32(task.evidenceHash, "evidenceHash");
    address(task.payer, "ledger payer");
    uint(task.amountAtomic, "ledger amount");
  }
  const validReconciliation = new Set([
    "SETTLEMENT_OBSERVED_LEDGER_CREATED", "PAID_PAYMENT_MATCH", "PROVEN_PAYMENT_MATCH",
  ]);
  if (reconciliation !== null) {
    check(task !== null && task !== undefined, "payment reconciliation needs chain state");
    check(validReconciliation.has(reconciliation.classification), "unknown reconciliation class");
    check(reconciliation.taskId === task!.taskId, "reconciliation task mismatch");
    check(address(reconciliation.ledger, "reconciliation ledger") ===
      address(task!.contract, "chain Ledger contract"), "reconciliation Ledger contract mismatch");
    check(reconciliation.chainId === task!.chainId, "reconciliation chain mismatch");
    check(hex32(reconciliation.descriptionHash, "reconciliation description") ===
      task!.descriptionHash, "reconciliation task description mismatch");
    check(address(reconciliation.payer, "reconciliation payer") !==
      "0x" + "0".repeat(40), "invalid payer");
    check(uint(reconciliation.amountAtomic, "reconciliation amount") > 0n, "invalid payment amount");
    if (status === "Paid" || status === "Proven") {
      check(address(reconciliation.payer, "reconciliation payer") ===
        address(task!.payer, "chain payer"), "reconciliation payer mismatch");
      check(uint(reconciliation.amountAtomic, "reconciliation amount") ===
        uint(task!.amountAtomic, "chain amount"), "reconciliation amount mismatch");
    }
    hex32(reconciliation.settlementRef, "verified settlement reference");
    hex32(reconciliation.paymentNonce, "verified EIP-3009 nonce");
    hex32(reconciliation.observedTransaction, "verified transaction");
    check(reconciliation.status === "READ_ONLY_NO_RECHARGE", "untrusted reconciliation status");
  }

  let paymentFact = "Unverified";
  let paymentStatus: TaskStatus = STATES.UNKNOWN;
  let discrepancy: string | null = null;
  let paymentFactValid = false;
  const chainAttested = status === "Paid" || status === "Proven";
  const paymentEvidence = reconciliation !== null;
  if (reconciliation !== null) {
    const expects: Record<string, string> = {
      Created: "SETTLEMENT_OBSERVED_LEDGER_CREATED",
      Paid: "PAID_PAYMENT_MATCH",
      Proven: "PROVEN_PAYMENT_MATCH",
    };
    if (reconciliation.classification !== expects[status as string]) {
      paymentStatus = STATES.CONFLICT;
      discrepancy = "reconciliation classification differs from on-chain task state";
    } else if (chainAttested &&
      reconciliation.settlementRef !== task!.settlementRef) {
      paymentStatus = STATES.CONFLICT;
      discrepancy = "on-chain attested settlement reference does not match independently observed transfer";
    } else {
      paymentFact = "VerifiedEip3009TransferAndNonce";
      paymentFactValid = true;
      paymentStatus = status === "Created" ? STATES.OBSERVED : STATES.RECORDED;
    }
  } else if (status === "Created" && attempt === "NotStarted") {
    paymentStatus = STATES.CREATED;
  }
  const anchored = status === "Proven" && task!.evidenceHash !== ZERO;
  let evidenceFact = anchored ? "HashAnchoredUnverified" : "NoVerifiedAnchor";
  if (evidence === "HashMatched") {
    check(anchored, "cannot mark a non-anchored task hash matched");
    evidenceFact = "HashMatchedToOnchainAnchor";
  }
  if (evidence === "HashMismatch") evidenceFact = "HashMismatch";
  if (evidence === "Unavailable" && anchored) evidenceFact = "HashVerificationUnavailable";
  if (anchored && evidence === "HashMatched" && paymentStatus === STATES.RECORDED) {
    paymentStatus = STATES.ANCHORED;
  }
  if (evidence === "HashMismatch" && anchored) {
    paymentStatus = STATES.CONFLICT;
    discrepancy = "local Evidence SHA-256 differs from on-chain anchor";
  }
  return {
    schemaVersion: PHASE0_SCHEMA,
    state: paymentStatus,
    ledgerState: status ?? "Unavailable",
    payment: {
      fact: paymentFact,
      recorderAttestationObserved: chainAttested,
      eventIndependentlyChecked: paymentFactValid,
      transaction: paymentEvidence ? reconciliation!.observedTransaction : null,
      authorizationNonce: paymentEvidence ? reconciliation!.paymentNonce : null,
      settlementRef: paymentEvidence ? reconciliation!.settlementRef : null,
    },
    evidence: {
      fact: evidenceFact,
      onchainHash: anchored ? task!.evidenceHash : null,
      bytesIndependentlyChecked: evidenceFact === "HashMatchedToOnchainAnchor",
    },
    execution: {
      authority: "ACP",
      status: "NOT_REPRESENTED_BY_LEDGER",
      semantics: "Ledger cannot schedule/resume/retry/claim ownership of Agent execution",
    },
    retryPolicy: {
      chargeAgain: false,
      action: paymentStatus === STATES.UNKNOWN || paymentStatus === STATES.CONFLICT
        ? "RECONCILE_READ_ONLY_NO_RECHARGE" : "NO_AUTOMATIC_PAYMENT_ACTION",
    },
    discrepancy,
    caveat: "Transfer + AuthorizationUsed proves observed token events, not finality or delivery. SHA-256 match proves bytes, not independent Agent correctness.",
  };
}

export { projectPhase0, PHASE0_SCHEMA, STATES };
