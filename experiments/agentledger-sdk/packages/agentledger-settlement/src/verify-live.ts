// SPDX-License-Identifier: Apache-2.0
// Live RPC-backed verification: checks chain ID 10143, receipt, canonical
// block inclusion, and returns a settlement receipt object.
import {
  verifyReceipt, validatePaymentResponse, type ReceiptLike,
  type PaymentReceipt, type VerifyReceiptExpected, hexQuantity,
  normalizeHash, requireMatch, type ReceiptLog,
  NETWORK, USDC, TRANSFER_TOPIC, ERC3009_EVENT0,
} from "./verify-receipt.js";

const DEFAULT_RPC = "https://testnet-rpc.monad.xyz";

export interface LiveVerifyOptions {
  minConfirmations?: number;
}

export async function verifyLive(
  expected: VerifyReceiptExpected,
  rpcUrl: string = DEFAULT_RPC,
  fetchImpl: typeof fetch = fetch,
  opts: LiveVerifyOptions = {},
): Promise<PaymentReceipt> {
  const target = new URL(rpcUrl);
  requireMatch(target.protocol === "https:" || target.hostname === "127.0.0.1",
    "RPC must use HTTPS (or loopback for tests)");
  let nextId = 0;
  async function rpc(method: string, params: unknown[] = []): Promise<unknown> {
    const response = await fetchImpl(target, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: ++nextId, method, params }),
      signal: AbortSignal.timeout(10000),
    });
    requireMatch(response.ok, `RPC ${method} HTTP ${response.status}`);
    const data = await response.json();
    requireMatch(!data.error && data.result !== undefined, `RPC ${method} failed`);
    return data.result;
  }
  const chainId = await rpc("eth_chainId") as string;
  requireMatch(BigInt(chainId) === 10143n, "RPC is not Monad Testnet");
  const expectedWithResponse = expected as { paymentResponse?: unknown };
  if (expectedWithResponse.paymentResponse) {
    validatePaymentResponse(expectedWithResponse.paymentResponse as unknown, expected);
  }
  const receipt = await rpc("eth_getTransactionReceipt",
    [normalizeHash(expected.transaction, "transaction")]) as ReceiptLike;
  if (!receipt || typeof receipt !== "object") {
    throw Error("transaction receipt missing; PaymentUnknown");
  }
  const height = hexQuantity(receipt.blockNumber, "receipt.blockNumber");
  const currentBlock = await rpc("eth_blockNumber") as string;
  const result = verifyReceipt(receipt, expected, currentBlock, opts);
  const canonical = await rpc("eth_getBlockByNumber",
    ["0x" + height.toString(16), false]) as { number?: string; hash?: string } | null;
  if (!canonical || typeof canonical !== "object") {
    throw Error("canonical block unavailable; PaymentUnknown");
  }
  requireMatch(BigInt(canonical.number!) === height, "canonical block height mismatch");
  requireMatch(normalizeHash(canonical.hash, "canonical block hash") === result.blockHash,
    "receipt block is no longer canonical; PaymentUnknown");
  return {
    ...result,
    canonicalBlockCheckedAtObservation: true,
    observedHeadBlockNumber: hexQuantity(currentBlock, "latest block").toString(),
    note: result.note +
      " Canonical block was checked at observation time; reorgs and finality still require follow-up.",
  };
}

export { verifyReceipt, validatePaymentResponse };
export type { PaymentReceipt, VerifyReceiptExpected, ReceiptLike, ReceiptLog };
export { NETWORK, USDC, TRANSFER_TOPIC, ERC3009_EVENT0, DEFAULT_RPC };
