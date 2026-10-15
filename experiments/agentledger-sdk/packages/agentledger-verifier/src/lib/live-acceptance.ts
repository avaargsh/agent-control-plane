// SPDX-License-Identifier: Apache-2.0
// Live Monad Testnet observation-only acceptance (read-only, no charge).
import { verifyLive, type VerifyReceiptExpected } from "@agentledger/settlement";

export interface LiveAcceptanceInput {
  transaction: string;
  payer: string;
  payTo: string;
  taskId: string;
  amountAtomic: string;
  paymentNonce?: string;
}

async function liveAcceptance(
  input: LiveAcceptanceInput,
  rpcUrl: string = "https://testnet-rpc.monad.xyz",
): Promise<{ verified: boolean; receipt?: unknown; error?: string }> {
  try {
    const expected: VerifyReceiptExpected = {
      transaction: input.transaction,
      payer: input.payer,
      payTo: input.payTo,
      taskId: input.taskId,
      network: "eip155:10143",
      amountAtomic: input.amountAtomic,
      ...(input.paymentNonce ? { paymentNonce: input.paymentNonce } : {}),
    };
    const result = await verifyLive(expected, rpcUrl);
    return { verified: true, receipt: result };
  } catch (error) {
    return { verified: false, error: (error as Error).message };
  }
}

export { liveAcceptance };
