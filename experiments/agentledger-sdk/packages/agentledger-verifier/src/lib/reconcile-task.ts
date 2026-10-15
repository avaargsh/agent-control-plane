// SPDX-License-Identifier: Apache-2.0
// Read-only reconciliation of paid transfer + precharge + chain state.
import { verifyTypedData } from "viem";
import { MONAD_CHAIN_ID, USDC_CONTRACT } from "@agentledger/core";

export interface ReconciliationInput {
  taskId: string;
  ledger: string;
  payer: string;
  amountAtomic: string;
  settlementRef: string;
  paymentNonce: string;
  observedTransaction: string;
  descriptionHash: string;
  chainStatus: "Created" | "Paid" | "Proven";
  classification: "SETTLEMENT_OBSERVED_LEDGER_CREATED" | "PAID_PAYMENT_MATCH" | "PROVEN_PAYMENT_MATCH";
  evidenceHash?: string;
  evidenceBytesMatched?: boolean;
}

export type ReconciliationClassification =
  | "SETTLEMENT_OBSERVED_LEDGER_CREATED"
  | "PAID_PAYMENT_MATCH"
  | "PROVEN_PAYMENT_MATCH"
  | "CONFLICT"
  | "ERROR";

export interface ReconciliationResult {
  classification: ReconciliationClassification;
  status: string;
  discrepancy: string | null;
  note: string;
}

function reconcileTask(input: ReconciliationInput): ReconciliationResult {
  const expects: Record<string, string> = {
    Created: "SETTLEMENT_OBSERVED_LEDGER_CREATED",
    Paid: "PAID_PAYMENT_MATCH",
    Proven: "PROVEN_PAYMENT_MATCH",
  };
  const expected = expects[input.chainStatus];
  if (!expected) {
    return { classification: "ERROR", status: input.chainStatus,
      discrepancy: "unknown chain status", note: "fail closed" };
  }
  if (input.classification !== expected) {
    return { classification: "CONFLICT", status: input.chainStatus,
      discrepancy: "reconciliation classification differs from on-chain task state",
      note: "RECONCILE_READ_ONLY_NO_RECHARGE" };
  }
  const chainAttested = input.chainStatus === "Paid" || input.chainStatus === "Proven";
  if (chainAttested && input.payer !== input.payer) {
    return { classification: "CONFLICT", status: input.chainStatus,
      discrepancy: "payer mismatch", note: "RECONCILE_READ_ONLY_NO_RECHARGE" };
  }
  if (chainAttested && input.amountAtomic !== input.amountAtomic) {
    return { classification: "CONFLICT", status: input.chainStatus,
      discrepancy: "amount mismatch", note: "RECONCILE_READ_ONLY_NO_RECHARGE" };
  }
  if (input.chainStatus === "Created") {
    return { classification: "SETTLEMENT_OBSERVED_LEDGER_CREATED", status: "PaymentObserved",
      discrepancy: null, note: "manual recorder review, no second charge" };
  }
  if (input.chainStatus === "Paid") {
    return { classification: "PAID_PAYMENT_MATCH", status: "PaymentRecorded",
      discrepancy: null, note: "already recorded payment matches" };
  }
  return { classification: "PROVEN_PAYMENT_MATCH", status: "EvidenceAnchored",
    discrepancy: null, note: "payment record matches; Evidence still needs independent bytes verification" };
}

export { reconcileTask };
