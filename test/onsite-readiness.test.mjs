// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";

test("onsite readiness is offline-first, wallet-free and refuses public acceptance claims", async () => {
  const { readinessPayload } = await import("../tools/onsite-check.mjs");
  const payload = readinessPayload({ missing: [], mode: "LOCAL_ONLY_NO_NETWORK" });
  assert.equal(payload.mode, "LOCAL_ONLY_NO_NETWORK");
  assert.equal(payload.safeToClaimPublicTestnetAcceptance, false);
  assert.equal(payload.walletRequired, false);
  assert.equal(payload.privateKeyRequired, false);
  assert.equal(payload.networkCalls, false);
  assert.equal(payload.payment.state, "PaymentUnknown");
  assert.equal(payload.payment.chargeAgain, false);
  assert.equal(payload.payment.actualUSDCSpend, false);
});

test("missing demo files flip safeToDemo to false with a visible missing list", async () => {
  const { readinessPayload } = await import("../tools/onsite-check.mjs");
  const payload = readinessPayload({ missing: ["examples/verifier/server.mjs"], mode: "LOCAL_ONLY_NO_NETWORK" });
  assert.equal(payload.safeToDemo, false);
  assert.deepEqual(payload.missingFiles, ["examples/verifier/server.mjs"]);
});
