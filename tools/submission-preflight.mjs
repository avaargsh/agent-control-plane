#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Offline-only preflight for a MANUAL hackathon repository handoff.
// It never clones, signs, uploads, pushes, submits to MOJO or contacts RPC.
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { listSecrets, missingRequiredFiles } from "./onsite-check.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
export const PRESENTATION_FILES = Object.freeze([
  "examples/verifier/public/index.html",
  "examples/verifier/tools/deploy.html",
  "slides/judge.md",
  "slides/slides.md",
]);
const HANDOFF_FILES = Object.freeze([
  "README.md", "docs/monad-blitz-submission.md",
  "docs/demo-walkthrough.md", "slides/judge.md",
  "src/AgentLedger.sol", "apps/pocket/App.tsx",
  "examples/verifier/public/index.html", "phase0/reviewed-testnet-deployments.json",
]);
const INTERNAL_REPO_URL = /github\.com\/[a-z0-9_.-]+\/(?:agent-control-plane|monad-hackathon)(?:\b|\/)/i;
const SENSITIVE_PATH = /(?:^|\/)(?:\.env(?:\..+)?|id_rsa|id_ed25519|[^/]+\.(?:pem|key|p12|pfx|secret))$/i;
const EXAMPLE_ENV = /(?:^|\/)\.env\.example$/i;

export function evaluateHandoff({ missingFiles = [], secretPaths = [], presentationFiles = {} } = {}) {
  const errors = [];
  if (missingFiles.length) errors.push({ code: "MISSING_FILES", files: [...missingFiles] });
  if (secretPaths.length) errors.push({ code: "SENSITIVE_FILES", files: [...secretPaths] });
  for (const [path, content] of Object.entries(presentationFiles)) {
    if (INTERNAL_REPO_URL.test(content)) errors.push({ code: "PUBLIC_INTERNAL_REPOSITORY_LINK", files: [path] });
  }
  return {
    schemaVersion: "agentledger.manual-submission-preflight.v1",
    mode: "OFFLINE_SOURCE_ONLY_NO_UPLOAD",
    readyForManualHandoff: errors.length === 0,
    checks: { sourceFiles: missingFiles.length === 0, noSensitiveFiles: secretPaths.length === 0,
      presentationLinksPortable: !errors.some(e => e.code === "PUBLIC_INTERNAL_REPOSITORY_LINK") },
    payment: { observedState: "PaymentUnknown", verifiedPublicUSDCSettlement: false,
      reviewedDeploymentAcceptance: "BLOCKED_NO_REVIEWED_DEPLOYMENT", automaticRecharge: false },
    externalActions: { gitPush: false, mojoSubmission: false, walletSigning: false, rpcRequests: false },
    errors,
  };
}

function trackedSensitivePaths() {
  const result = spawnSync("git", ["-C", root, "ls-files", "-z"], { encoding: "utf8" });
  // Without Git metadata the handoff cannot assert that a sensitive file is untracked.
  if (result.error || result.status !== 0) return ["GIT_TRACKING_UNAVAILABLE"];
  return result.stdout.split("\0").filter(Boolean).filter(path =>
    SENSITIVE_PATH.test(path) && !EXAMPLE_ENV.test(path));
}

async function main() {
  const missing = await missingRequiredFiles();
  for (const path of HANDOFF_FILES) {
    try { await readFile(new URL("../" + path, import.meta.url)); }
    catch { if (!missing.includes(path)) missing.push(path); }
  }
  const presentationFiles = {};
  for (const path of PRESENTATION_FILES) {
    try { presentationFiles[path] = await readFile(new URL("../" + path, import.meta.url), "utf8"); }
    catch { if (!missing.includes(path)) missing.push(path); }
  }
  const localSecrets = await listSecrets();
  const secretPaths = [...new Set([...localSecrets, ...trackedSensitivePaths()])].sort();
  const report = evaluateHandoff({ missingFiles: missing.sort(), secretPaths, presentationFiles });
  process.stdout.write(JSON.stringify(report, null, 2) + "\n");
  if (!report.readyForManualHandoff) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  await main();
}
