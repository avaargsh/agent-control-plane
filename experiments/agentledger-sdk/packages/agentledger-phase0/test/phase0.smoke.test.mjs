// SPDX-License-Identifier: Apache-2.0
import { strictEqual, ok } from "node:assert";
import { projectPhase0, STATES } from "../dist/index.js";

// Case 1: no task on chain -> PaymentUnknown
const r1 = projectPhase0({
  task: null,
  attempt: "NotStarted",
  reconciliation: null,
  evidence: "NotSupplied",
});
strictEqual(r1.state, STATES.UNKNOWN);
strictEqual(r1.schemaVersion, "agentledger.phase0.status.v1");
ok(r1.retryPolicy.chargeAgain === false);

// Case 2: chain task Created, no reconciliation -> Created
const r2 = projectPhase0({
  task: {
    chainId: 10143,
    status: "Created",
    taskId: "0x" + "01".repeat(32),
    descriptionHash: "0x" + "02".repeat(32),
    settlementRef: "0x" + "03".repeat(32),
    evidenceHash: "0x" + "00".repeat(32),
    payer: "0x" + "aa".repeat(20),
    amountAtomic: "1000",
    contract: "0x" + "cc".repeat(20),
  },
  attempt: "NotStarted",
  reconciliation: null,
  evidence: "NotSupplied",
});
strictEqual(r2.state, STATES.CREATED);

console.log("agentledger-phase0: smoke project OK");
