// SPDX-License-Identifier: Apache-2.0
// Isolated Monad Testnet x402 task-scoped payment gate.
// Payment receipt capture is pending verification, NOT a Paid ledger mutation.
// No private keys or chain writes are handled by this server.
import express from "express";
import { paymentMiddleware } from "@x402/express";
import { x402ResourceServer, HTTPFacilitatorClient } from "@x402/core/server";
import { ExactEvmScheme } from "@x402/evm/exact/server";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const NETWORK = "eip155:10143";
const USDC = "0x534b2f3A21130d7a60830c2Df862319e593943A3";
const TX_RE = /^0x[0-9a-fA-F]{64}$/;
const TASK_RE = /^0x[0-9a-fA-F]{64}$/;
const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
const facilitatorUrl = process.env.X402_FACILITATOR_URL || "https://x402-facilitator.molandak.org";
const payTo = process.env.PAY_TO_ADDRESS;
const taskId = process.env.TASK_ID?.toLowerCase();
if (!ADDRESS_RE.test(payTo || "")) throw new Error("PAY_TO_ADDRESS must be an EVM address");
if (!TASK_RE.test(taskId || "")) throw new Error("TASK_ID must be 32 bytes");
if (new URL(facilitatorUrl).protocol !== "https:") throw new Error("Facilitator must use HTTPS");

// Only one exact-price, task-addressed route in this hackathon process.
const amountAtomic = "1000"; // $0.001 USDC (6 decimals)
const resourcePath = `/tasks/${taskId}/run`;
const receiptsDir = fileURLToPath(new URL("../../receipts/", import.meta.url));
mkdirSync(receiptsDir, { recursive: true, mode: 0o700 });

const support = await fetch(new URL("/supported", facilitatorUrl), {
  signal: AbortSignal.timeout(10000),
});
if (!support.ok) throw new Error("facilitator /supported failed: " + support.status);
const supportedKinds = (await support.json()).kinds || [];
if (!Array.isArray(supportedKinds) || !supportedKinds.some(
  s => s.network === NETWORK && (s.scheme === "exact" || s.scheme === "v2-eip155-exact")
)) throw new Error("facilitator lacks Monad Testnet exact support");

const moneyParser = new ExactEvmScheme();
moneyParser.registerMoneyParser(async (price, network) => {
  if (network !== NETWORK) return null;
  const decimal = String(price);
  if (!/^\d+(\.\d{1,6})?$/.test(decimal)) throw new Error("unsupported price");
  const [major, fractional = ""] = decimal.split(".");
  const amount = BigInt(major) * 1000000n + BigInt(fractional.padEnd(6, "0"));
  if (amount <= 0n) throw new Error("zero price forbidden");
  return { amount: amount.toString(), asset: USDC, extra: { name: "USDC", version: "2" } };
});

const server = new x402ResourceServer(new HTTPFacilitatorClient({ url: facilitatorUrl }))
  .register(NETWORK, moneyParser);

// Capture only post-settlement metadata. This is recoverable information,
// not trustless verification and not proof of task authorization.
// A crash between on-chain settlement and this write still needs reconciliation
// from the payer's PAYMENT-RESPONSE transaction hash.
server.onAfterSettle(async ({ result }) => {
  if (result?.success !== true || result.network !== NETWORK ||
      !TX_RE.test(result.transaction || "") || !ADDRESS_RE.test(result.payer || "")) {
    console.error("UNRECORDED settlement: unexpected result; manual reconciliation required");
    return;
  }
  const transaction = result.transaction.toLowerCase();
  const pending = {
    schemaVersion: "agentledger.pending-x402.v1",
    taskId, network: NETWORK, transaction, payer: result.payer.toLowerCase(),
    payTo: payTo.toLowerCase(), token: USDC, amountAtomic,
    paymentResponse: {
      success: true, network: result.network, transaction,
      payer: result.payer.toLowerCase(), ...(result.amount ? { amount: String(result.amount) } : {}),
    },
    recordedAt: new Date().toISOString(),
    verified: false,
    note: "Untrusted until RPC verification and independent task authorization check",
  };
  try {
    writeFileSync(`${receiptsDir}/${transaction}.json`, JSON.stringify(pending, null, 2) + "\n",
      { flag: "wx", mode: 0o600 });
  } catch (error) {
    if (error.code !== "EEXIST") {
      console.error("SETTLED BUT NOT JOURNALED; recover from x402 PAYMENT-RESPONSE:", transaction);
    }
  }
});

const app = express();
app.get("/healthz", (_req, res) => res.json({ ok: true, network: NETWORK }));
app.use(paymentMiddleware({
  [`GET ${resourcePath}`]: {
    accepts: { scheme: "exact", network: NETWORK, price: "$0.001", payTo },
    description: "Paid demo for a fixed AgentLedger task; no agent execution side effect",
    mimeType: "application/json",
  },
}, server));

app.get(resourcePath, (_req, res) => res.json({
  taskId,
  value: "AgentLedger task-scoped paid API demo",
  note: "This is NOT an Agent run; payment must be independently verified and committed to ledger.",
}));

app.listen(Number(process.env.PORT || 4021), "127.0.0.1", () => {
  console.log("x402 task demo listening on http://127.0.0.1:" + (process.env.PORT || 4021) + resourcePath);
});
