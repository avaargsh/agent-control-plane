// SPDX-License-Identifier: Apache-2.0
// AgentLedger core: shared types, constants and validation primitives.
// Dependency-free so every package entrypoint agrees on one schema.
export const MONAD_CHAIN_ID = 10143;
export const MONAD_NETWORK = "eip155:10143";
export const USDC_CONTRACT = "0x534b2f3A21130d7a60830c2Df862319e593943A3";

export const HEX32 = /^0x[0-9a-fA-F]{64}$/;
export const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
export const HEX_QUANTITY = /^0x[0-9a-fA-F]+$/;
export const UINT_DECIMAL = /^(0|[1-9][0-9]*)$/;

export const SCHEMA_EVIDENCE_V1 = "agentledger.evidence.v1";
export const SCHEMA_SETTLEMENT_V1 = "agentledger.settlement.v1";
export const SCHEMA_ADMISSION_V1 = "agentledger.admission.v1";
export const SCHEMA_PRECHARGE_V1 = "agentledger.precharge.v1";
export const SCHEMA_PHASE0_STATUS_V1 = "agentledger.phase0.status.v1";
export const SCHEMA_TASK_PAYMENT_V1 = "agentledger.task-payment.v1";

export const ZERO_ADDRESS = "0x" + "0".repeat(40);
export const ZERO_HASH32 = "0x" + "0".repeat(64);

export type TaskStatus =
  | "Created"
  | "PaymentUnknown"
  | "PaymentObserved"
  | "PaymentRecorded"
  | "EvidenceAnchored"
  | "Conflict";

export interface TaskPaymentApprovalQuote {
  schemaVersion: "agentledger.task-payment.v1";
  chainId: number;
  ledger: string;           // verifying contract address
  taskId: string;           // bytes32
  descriptionHash: string;  // bytes32
  token: string;            // address (USDC)
  payer: string;            // address
  payTo: string;            // address
  amountAtomic: string;     // decimal uint string
  expiresAt: string;        // decimal uint64 string
  nonce: string;            // bytes32
}

export interface Eip712ApprovalData {
  address: string;
  domain: {
    name: string;
    version: string;
    chainId: number;
    verifyingContract: string;
  };
  primaryType: string;
  types: Record<string, Array<{ name: string; type: string }>>;
  message: Record<string, unknown>;
}

export interface PaymentReceipt {
  schemaVersion: typeof SCHEMA_SETTLEMENT_V1;
  taskId: string;
  network: typeof MONAD_NETWORK;
  token: string;
  transaction: string;
  payer: string;
  payTo: string;
  amountAtomic: string;
  settlementRef: string;
  logIndex: number;
  blockNumber: string;
  blockHash: string;
  confirmations: string;
  paymentNonce?: string;
}

export interface EvidenceV1 {
  completedAt: string;      // ISO UTC with milliseconds
  executor: string;
  inputSha256: string;      // bytes32
  modelOrTool: string;
  outputSha256: string;     // bytes32
  schemaVersion: typeof SCHEMA_EVIDENCE_V1;
  taskId: string;           // bytes32
}

export function assertHex32(value: unknown, label: string): string {
  if (typeof value !== "string" || !HEX32.test(value)) {
    throw new Error(`invalid ${label}: must be lowercase 0x-prefixed bytes32`);
  }
  return value.toLowerCase();
}

export function assertAddress(value: unknown, label: string): string {
  if (typeof value !== "string" || !ADDRESS.test(value)) {
    throw new Error(`invalid ${label}: must be a valid EVM address`);
  }
  return value.toLowerCase();
}

export function assertUintString(value: unknown, label: string): bigint {
  if (typeof value !== "string" || !UINT_DECIMAL.test(value)) {
    throw new Error(`invalid ${label}: must be a nonnegative decimal integer string`);
  }
  const n = BigInt(value);
  if (n < 0n) throw new Error(`invalid ${label}: negative`);
  return n;
}

export function assertPositiveUintString(value: unknown, label: string): bigint {
  const n = assertUintString(value, label);
  if (n <= 0n) throw new Error(`invalid ${label}: zero forbidden`);
  return n;
}

export function isHex32(value: unknown): value is string {
  return typeof value === "string" && HEX32.test(value);
}

export function isAddress(value: unknown): value is string {
  return typeof value === "string" && ADDRESS.test(value);
}

export const APPROVAL_TYPES: Record<string, Array<{ name: string; type: string }>> = {
  TaskPaymentApproval: [
    { name: "taskId", type: "bytes32" },
    { name: "descriptionHash", type: "bytes32" },
    { name: "token", type: "address" },
    { name: "payTo", type: "address" },
    { name: "amountAtomic", type: "uint256" },
    { name: "expiresAt", type: "uint64" },
    { name: "nonce", type: "bytes32" },
  ],
};
