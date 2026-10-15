// SPDX-License-Identifier: Apache-2.0
import { strictEqual, ok } from "node:assert";
import { guardConfig, parseHeader } from "../dist/index.js";

// Config validation
const config = guardConfig({
  ledger: "0x" + "11".repeat(20),
  payTo: "0x" + "22".repeat(20),
  taskId: "0x" + "01".repeat(32),
  publicOrigin: "http://127.0.0.1:4021",
});
strictEqual(config.resourcePath, "/tasks/" + "0x" + "01".repeat(32) + "/run");
strictEqual(config.ledger, "0x" + "11".repeat(20));

// Header parsing
const header = Buffer.from(JSON.stringify({ schemaVersion: "agentledger.task-payment.v1" })).toString("base64");
const parsed = parseHeader(header, "task approval");
strictEqual(parsed.schemaVersion, "agentledger.task-payment.v1");

console.log("agentledger-x402: smoke import + guard OK");
