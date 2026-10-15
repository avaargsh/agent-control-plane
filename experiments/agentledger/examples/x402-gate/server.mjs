// SPDX-License-Identifier: Apache-2.0
// Live x402-paid endpoint demo; DOES NOT yet write payment receipts to AgentLedger.
import express from "express";
import { paymentMiddleware, x402ResourceServer } from "@x402/express";
import { HTTPFacilitatorClient } from "@x402/core/server";
import { ExactEvmScheme } from "@x402/evm/exact/server";

const NETWORK = "eip155:10143";
const USDC = "0x534b2f3A21130d7a60830c2Df862319e593943A3";
const facilitatorUrl = process.env.X402_FACILITATOR_URL || "https://x402-facilitator.molandak.org";
const payTo = process.env.PAY_TO_ADDRESS;
if (!/^0x[0-9a-fA-F]{40}$/.test(payTo || "")) {
  throw new Error("PAY_TO_ADDRESS must be an EVM address");
}
if (new URL(facilitatorUrl).protocol !== "https:") {
  throw new Error("X402_FACILITATOR_URL must use HTTPS");
}

// Never start a paid route against an unverified network/scheme.
const support = await fetch(new URL("/supported", facilitatorUrl), {
  signal: AbortSignal.timeout(10000),
});
if (!support.ok) throw new Error("facilitator /supported failed: " + support.status);
const supportedKinds = (await support.json()).kinds || [];
if (!Array.isArray(supportedKinds) || !supportedKinds.some(
  (s) => s.network === NETWORK && (s.scheme === "exact" || s.scheme === "v2-eip155-exact")
)) throw new Error("facilitator lacks Monad Testnet exact support");

const moneyParser = new ExactEvmScheme();
// Explicit asset avoids ambiguity across SDK versions.
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

const app = express();
app.get("/healthz", (_req, res) => res.json({ ok: true, paymentMode: "testnet-x402" }));
app.use(paymentMiddleware({
  "GET /paid-data": {
    accepts: { scheme: "exact", network: NETWORK, price: "$0.001", payTo },
    description: "One paid demo API response (not an execution attestation)",
    mimeType: "application/json",
  },
}, server));

app.get("/paid-data", (_req, res) => res.json({
  value: "AgentLedger paid API demo",
  note: "This endpoint tests x402; settlement-to-task binding is NOT implemented.",
}));

app.listen(Number(process.env.PORT || 4021), "127.0.0.1", () => {
  console.log("x402 demo listening on http://127.0.0.1:" + (process.env.PORT || 4021));
});
