// SPDX-License-Identifier: Apache-2.0
// Express x402 HTTP 402 payment middleware factory for AgentLedger.
import express from "express";
import { paymentMiddleware } from "@x402/express";
import { x402ResourceServer, HTTPFacilitatorClient } from "@x402/core/server";
import { ExactEvmScheme } from "@x402/evm/exact/server";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  guardConfig, authorizeHttpRequest, APPROVAL_HEADER, PAYMENT_HEADER,
  AMOUNT_ATOMIC, type GuardConfig,
} from "./challenge-guard.js";
import { USDC_CONTRACT } from "@agentledger/core";

interface X402Options {
  ledger: string;
  payTo: string;
  taskId: string;
  publicOrigin?: string;
  port?: number;
  facilitatorUrl?: string;
}

function createX402App(options: X402Options): ReturnType<typeof express> {
  const NETWORK = "eip155:10143";
  const USDC = USDC_CONTRACT.toLowerCase();
  const facilitatorUrl = options.facilitatorUrl ?? "https://x402-facilitator.molandak.org";
  const payTo = options.payTo;
  const taskId = options.taskId?.toLowerCase();

  const addressRe = /^0x[0-9a-fA-F]{40}$/;
  const taskRe = /^0x[0-9a-fA-F]{64}$/;
  if (!addressRe.test(payTo)) throw new Error("payTo must be an EVM address");
  if (!taskRe.test(taskId ?? "")) throw new Error("taskId must be 32 bytes");
  if (new URL(facilitatorUrl).protocol !== "https:") throw new Error("Facilitator must use HTTPS");

  const amountAtomic = "1000";
  const guard = guardConfig({
    ledger: options.ledger, payTo, taskId,
    publicOrigin: options.publicOrigin ?? `http://127.0.0.1:${options.port ?? 4021}`,
  });
  const resourcePath = guard.resourcePath;
  const receiptsDir = fileURLToPath(new URL("../../receipts/", import.meta.url));
  mkdirSync(receiptsDir, { recursive: true, mode: 0o700 });

  const moneyParser = new ExactEvmScheme();
  (moneyParser as any).registerMoneyParser(async (price: string, network: string) => {
    if (network !== NETWORK) return null;
    const decimal = String(price);
    if (!/^\d+(\.\d{1,6})?$/.test(decimal)) throw new Error("unsupported price");
    const [major, fractional = ""] = decimal.split(".");
    const amount = BigInt(major) * 1000000n + BigInt(fractional.padEnd(6, "0"));
    if (amount <= 0n) throw new Error("zero price forbidden");
    return { amount: amount.toString(), asset: USDC, extra: { name: "USDC", version: "2" } };
  });

  const server = new x402ResourceServer(new HTTPFacilitatorClient({ url: facilitatorUrl }))
    .register(NETWORK, moneyParser as any);

  (server as any).onAfterSettle(async ({ result, paymentPayload }: { result?: unknown; paymentPayload?: unknown }) => {
    const res = result as { success?: boolean; network?: string; transaction?: string; payer?: string; amount?: unknown } | undefined;
    if (res?.success !== true || res.network !== NETWORK ||
        !res.transaction || !/^0x[0-9a-fA-F]{64}$/.test(res.transaction) ||
        !res.payer || !/^0x[0-9a-fA-F]{40}$/.test(res.payer)) {
      console.error("UNRECORDED settlement: unexpected result; manual reconciliation required");
      return;
    }
    const transaction = res.transaction.toLowerCase();
    const pending = {
      schemaVersion: "agentledger.pending-x402.v1",
      taskId, network: NETWORK, transaction, payer: res.payer.toLowerCase(),
      payTo: payTo.toLowerCase(), token: USDC, amountAtomic,
      paymentNonce: typeof (paymentPayload as { payload?: { authorization?: { nonce?: string } } })?.payload?.authorization?.nonce === "string" ?
        (paymentPayload as { payload: { authorization: { nonce: string } } }).payload.authorization.nonce.toLowerCase() : null,
      paymentResponse: {
        success: true, network: res.network, transaction,
        payer: res.payer.toLowerCase(), ...(res.amount ? { amount: String(res.amount) } : {}),
      },
      recordedAt: new Date().toISOString(),
      verified: false,
      note: "Untrusted until RPC verification and independent task/quote authorization check.",
    };
    try {
      writeFileSync(`${receiptsDir}/${transaction}.json`, JSON.stringify(pending, null, 2) + "\n",
        { flag: "wx", mode: 0o600 });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
        console.error("SETTLED BUT NOT JOURNALED; recover from x402 PAYMENT-RESPONSE:", transaction);
      }
    }
  });

  const app = express();
  app.get(resourcePath, async (req: express.Request, res: express.Response, next: express.NextFunction) => {
    try {
      await authorizeHttpRequest(req.headers as Record<string, string>, guard);
      next();
    } catch (error) {
      const message = (error as Error).message;
      const conflict = /reservation|reconcile|locked/i.test(message);
      res.status(conflict ? 409 : 403).json({
        error: conflict ? "Payment attempt exists or needs reconciliation; no second charge" :
          "Task or payment authorization rejected; settlement not attempted",
      });
    }
  });
  app.get("/healthz", (_req: express.Request, res: express.Response) => res.json({ ok: true, network: NETWORK }));
  app.use((paymentMiddleware as any)({
    [`GET ${resourcePath}`]: {
      accepts: { scheme: "exact", network: NETWORK, price: "$0.001", payTo },
      description: "Paid demo for a fixed AgentLedger task; no agent execution side effect",
      mimeType: "application/json",
    },
  } as any), server as any);
  app.get(resourcePath, (_req: express.Request, res: express.Response) => res.json({
    taskId,
    value: "AgentLedger task-scoped paid API demo",
    note: "This is NOT an Agent run; payment must be independently verified and committed to ledger.",
  }));

  return app;
}

function startX402Server(options: X402Options): void {
  const app = createX402App(options);
  const port = options.port ?? 4021;
  app.listen(port, "127.0.0.1", () => {
    console.log("x402 task demo listening on http://127.0.0.1:" + port + "/tasks/" + options.taskId?.toLowerCase() + "/run");
  });
}

export { createX402App, startX402Server };
export type { X402Options };
