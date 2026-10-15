// SPDX-License-Identifier: Apache-2.0
// EIP-712 payer signature verification for TaskPaymentApproval.
import { hashTypedData, verifyTypedData } from "viem";
import {
  buildApprovalData, MONAD_CHAIN_ID, USDC_CONTRACT,
  type Eip712ApprovalData,
} from "@agentledger/core";

const TRANSFER_TYPES = {
  TransferWithAuthorization: [
    { name: "from", type: "address" }, { name: "to", type: "address" },
    { name: "value", type: "uint256" }, { name: "validAfter", type: "uint256" },
    { name: "validBefore", type: "uint256" }, { name: "nonce", type: "bytes32" },
  ],
} as const;

export interface TaskQuote {
  schemaVersion: string;
  chainId: number;
  ledger: string;
  taskId: string;
  descriptionHash: string;
  token: string;
  payer: string;
  payTo: string;
  amountAtomic: string;
  expiresAt: string;
  nonce: string;
  signature: string;
}

export interface VerifiedQuote extends Eip712ApprovalData {
  quote: TaskQuote;
}

function parseQuoteHeader(raw: string): TaskQuote {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 8192) {
    throw new Error("task approval missing or too large");
  }
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(raw) || raw.length % 4 === 1) {
    throw new Error("task approval invalid base64");
  }
  const decoded = Buffer.from(raw, "base64");
  if (decoded.toString("base64").replace(/=+$/, "") !== raw.replace(/=+$/, "")) {
    throw new Error("task approval noncanonical base64");
  }
  const json = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(decoded));
  if (json === null || typeof json !== "object" || Array.isArray(json)) {
    throw new Error("task approval must be an object");
  }
  const quote = json as TaskQuote;
  if (quote.schemaVersion !== "agentledger.task-payment.v1") {
    throw new Error("invalid approval schema");
  }
  if (quote.chainId !== MONAD_CHAIN_ID) {
    throw new Error("wrong approval network");
  }
  if (quote.token.toLowerCase() !== USDC_CONTRACT.toLowerCase()) {
    throw new Error("wrong USDC token");
  }
  return quote;
}

async function verifyTaskQuoteSignature(
  quote: TaskQuote,
  ledgerAddress: string,
): Promise<VerifiedQuote> {
  const typed = buildApprovalData(quote);
  const valid = await verifyTypedData({
    address: typed.address as `0x${string}`,
    domain: typed.domain as any,
    types: typed.types as any,
    primaryType: typed.primaryType,
    message: typed.message as any,
    signature: quote.signature as `0x${string}`,
  }) as boolean;
  if (!valid) throw new Error("payer task signature invalid");
  if (typed.domain.verifyingContract !== ledgerAddress) {
    throw new Error("quote ledger mismatch");
  }
  return { ...typed, quote };
}

export interface EIP3009Auth {
  from: string;
  to: string;
  value: string;
  validAfter: string;
  validBefore: string;
  nonce: string;
}

async function verifyEIP3009Payment(
  auth: EIP3009Auth,
  signature: string,
  payee: string,
  expectedAmount: string,
  chainId: number = MONAD_CHAIN_ID,
  usdcAddress: string = USDC_CONTRACT,
): Promise<void> {
  const valid = await verifyTypedData({
    address: auth.from as `0x${string}`,
    domain: { name: "USDC", version: "2", chainId, verifyingContract: usdcAddress } as any,
    primaryType: "TransferWithAuthorization",
    types: TRANSFER_TYPES as any,
    message: { from: auth.from, to: auth.to, value: auth.value,
      validAfter: auth.validAfter, validBefore: auth.validBefore, nonce: auth.nonce },
    signature: signature as `0x${string}`,
  }) as boolean;
  if (!valid) throw new Error("EIP-3009 payment signature invalid");
  if (auth.to.toLowerCase() !== payee.toLowerCase()) {
    throw new Error("payment recipient mismatch");
  }
  if (auth.value !== expectedAmount) {
    throw new Error("payment amount mismatch");
  }
}

export { parseQuoteHeader, verifyTaskQuoteSignature, verifyEIP3009Payment };
