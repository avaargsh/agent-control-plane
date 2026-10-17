// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { evaluateHandoff } from "../tools/submission-preflight.mjs";

test("manual preflight leaves payment unknown and never uploads or signs", () => {
  const report = evaluateHandoff({ presentationFiles: {
    "slides/judge.md": "<h1>AgentLedger</h1>",
    "examples/verifier/public/index.html": "<h1>Pay → Execute → Prove</h1>",
  } });
  assert.equal(report.readyForManualHandoff, true);
  assert.equal(report.payment.observedState, "PaymentUnknown");
  assert.equal(report.payment.verifiedPublicUSDCSettlement, false);
  assert.equal(report.externalActions.gitPush, false);
  assert.equal(report.externalActions.mojoSubmission, false);
  assert.equal(report.externalActions.rpcRequests, false);
});

test("manual preflight rejects exposed internal source and future submission links", () => {
  const report = evaluateHandoff({ presentationFiles: {
    "slides/judge.md": "github.com/author/agent-control-plane",
    "slides/slides.md": "https://github.com/author/monad-hackathon",
  } });
  assert.equal(report.readyForManualHandoff, false);
  assert.equal(report.errors.filter(e => e.code === "PUBLIC_INTERNAL_REPOSITORY_LINK").length, 2);
});

test("manual preflight blocks missing source and sensitive tracked path", () => {
  const report = evaluateHandoff({ missingFiles: ["slides/judge.md"], secretPaths: ["wallet.pem"] });
  assert.equal(report.readyForManualHandoff, false);
  assert.deepEqual(report.errors.map(e => e.code), ["MISSING_FILES", "SENSITIVE_FILES"]);
});
