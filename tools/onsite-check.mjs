#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Offline-first demo readiness check. No wallet, no private key, no
// signature, no transaction. Reads local repository state and, only with
// --network, performs an additional GET-only /api/healthz probe.
import { access, readdir } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = new URL("../", import.meta.url);
const at = (relative) => fileURLToPath(new URL(relative, root));

const REQUIRED_FILES = [
  "src/AgentLedger.sol",
  "tools/evidence.mjs",
  "tools/onsite-check.mjs",
  "examples/verifier/server.mjs",
  "examples/verifier/lib/task-reader.mjs",
  "examples/verifier/public/evidence-browser.mjs",
  "examples/x402-gate/server.mjs",
  "sdk/package.json",
  "apps/pocket/package.json",
  "phase0/reviewed-testnet-deployments.json",
];
const SECRET_SUFFIXES = [".secret", ".env", ".env.local", ".key", ".pem"];

export function readinessPayload({ missing, mode }) {
  const safeToDemo = missing.length === 0;
  return {
    schemaVersion: "agentledger.readiness.v1",
    mode, // LOCAL_ONLY_NO_NETWORK | VERIFIER_HEALTHZ_NO_WALLET
    safeToDemo,
    missingFiles: missing,
    safeToClaimPublicTestnetAcceptance: false,
    reviewedTestnetDeployments: false,
    walletRequired: false,
    privateKeyRequired: false,
    networkCalls: mode === "VERIFIER_HEALTHZ_NO_WALLET",
    payment: {
      state: "PaymentUnknown",
      chargeAgain: false,
      actualUSDCSpend: false,
      note:
        "Unknown is not unpaid. Hash match is not execution correctness. " +
        "Precheck readiness never implies that a USDC payment happened.",
    },
  };
}

export async function listSecrets() {
  const found = [];
  try {
    for (const entry of await readdir(fileURLToPath(root), { withFileTypes: true })) {
      if (!entry.isFile()) continue;
      if (SECRET_SUFFIXES.some((suffix) => entry.name.endsWith(suffix))) found.push(entry.name);
    }
  } catch { /* unreadable root: fail closed below */ }
  return found;
}

export async function missingRequiredFiles() {
  const missing = [];
  for (const file of REQUIRED_FILES) {
    try { await access(at(file)); } catch { missing.push(file); }
  }
  return missing;
}

async function healthz(base) {
  const url = new URL("/api/healthz", base);
  if (url.protocol !== "https:" && url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
    throw new Error("verifier probe must be HTTPS or loopback");
  }
  const response = await fetch(url, { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error("verifier HTTP " + response.status);
  const health = await response.json();
  if (health?.chainId !== 10143 || health?.mode !== "read-only") {
    throw new Error("verifier healthz chain/mode mismatch");
  }
  return health;
}

async function main() {
  const network = process.argv.includes("--network");
  const baseArg = process.argv[process.argv.indexOf("--network") + 1];
  const base = network && baseArg && !baseArg.startsWith("--") ? baseArg : "http://127.0.0.1:4321";
  const missing = await missingRequiredFiles();
  const secrets = await listSecrets();
  const safeToDemo = missing.length === 0 && secrets.length === 0;
  const payload = readinessPayload({ missing: [...missing, ...secrets], mode: network ? "VERIFIER_HEALTHZ_NO_WALLET" : "LOCAL_ONLY_NO_NETWORK" });
  payload.safeToDemo = safeToDemo;
  if (missing.length) payload.missingFiles = missing;
  if (secrets.length) payload.unsafeSecretFiles = secrets;
  if (network) {
    try {
      payload.verifier = { reachable: true, ...(await healthz(base)) };
    } catch (error) {
      payload.verifier = { reachable: false, error: String(error.message || error) };
      payload.healthyVerifier = false;
    }
  }
  const report = JSON.stringify(payload, null, 2);
  console.log(report);
  process.exitCode = safeToDemo ? 0 : 1;
  return payload;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
