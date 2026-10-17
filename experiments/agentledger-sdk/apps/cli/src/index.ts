#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// AgentLedger thin CLI entrypoint over the SDK packages.
// Experimental: Monad Testnet only; not ACP v0.1.
import { createX402App, startX402Server } from "@agentledger/x402";
import { createVerifierServer } from "@agentledger/verifier";
import { projectPhase0, STATES } from "@agentledger/phase0";

const command = process.argv[2];

async function main() {
  switch (command) {
    case "phase0": {
      const result = projectPhase0({
        task: null,
        attempt: "NotStarted",
        reconciliation: null,
        evidence: "NotSupplied",
      });
      console.log(JSON.stringify(result, null, 2));
      break;
    }
    case "verifier": {
      const port = Number(process.env.PORT || 4321);
      const server = createVerifierServer();
      server.listen(port, "127.0.0.1", () =>
        console.log("agentledger verifier: http://127.0.0.1:" + port));
      break;
    }
    case "x402": {
      const port = Number(process.env.PORT || 4021);
      const app = createX402App({
        ledger: process.env.LEDGER_ADDRESS ?? "",
        payTo: process.env.PAY_TO_ADDRESS ?? "",
        taskId: process.env.TASK_ID ?? "",
        publicOrigin: process.env.PUBLIC_ORIGIN,
        port,
      });
      app.listen(port, "127.0.0.1", () =>
        console.log("agentledger x402: http://127.0.0.1:" + port));
      break;
    }
    case "states": {
      console.log(JSON.stringify([...Object.values(STATES)], null, 2));
      break;
    }
    default:
      console.error("Usage: agentledger <phase0|verifier|x402|states>");
      process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error("agentledger CLI error:", err.message);
  process.exitCode = 1;
});
