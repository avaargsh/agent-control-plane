#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Non-custodial, read-only discovery. No private keys or payments.
const base = process.env.X402_FACILITATOR_URL || "https://x402-facilitator.molandak.org";
const network = "eip155:10143";
const url = new URL("/supported", base);
if (url.protocol !== "https:") throw new Error("facilitator must use HTTPS");
try {
  const response = await fetch(url, { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error("facilitator HTTP " + response.status);
  const result = await response.json();
  const kinds = Array.isArray(result.kinds) ? result.kinds : [];
  console.log(JSON.stringify({ facilitator: url.origin, kinds }, null, 2));
  const supported = kinds.some((kind) =>
    kind.network === network &&
    (kind.scheme === "exact" || kind.scheme === "v2-eip155-exact")
  );
  if (!supported) throw new Error("Monad Testnet x402 exact NOT advertised; fail closed");
  console.log("PASS: Monad Testnet x402 exact advertised");
} catch (error) {
  console.error("PREFLIGHT FAILED: " + error.message);
  process.exitCode = 1;
}
