// SPDX-License-Identifier: Apache-2.0
import { strictEqual, ok } from "node:assert";
import { verifyReceipt } from "../dist/index.js";

const receipt = {
  status: "0x1",
  transactionHash: "0x" + "aa".repeat(32),
  blockHash: "0x" + "bb".repeat(32),
  blockNumber: "0x1234",
  transactionIndex: "0x0",
  logs: [{
    removed: false,
    address: "0x534b2f3A21130d7a60830c2Df862319e593943A3",
    topics: [
      "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
      "0x000000000000000000000000" + "11".repeat(20),
      "0x000000000000000000000000" + "22".repeat(20),
    ],
    data: "0x00000000000000000000000000000000000000000000000000000000000003e8",
    logIndex: "0x0",
    transactionHash: "0x" + "aa".repeat(32),
    blockHash: "0x" + "bb".repeat(32),
  }],
};

const expected = {
  transaction: receipt.transactionHash,
  payer: "0x" + "11".repeat(20),
  payTo: "0x" + "22".repeat(20),
  taskId: "0x" + "01".repeat(32),
  network: "eip155:10143",
  amountAtomic: "1000",
};

const result = verifyReceipt(receipt, expected, "0x1235");
strictEqual(result.schemaVersion, "agentledger.settlement.v1");
strictEqual(result.taskId, expected.taskId);
strictEqual(result.network, "eip155:10143");
strictEqual(result.payer, expected.payer);
strictEqual(result.payTo, expected.payTo);
strictEqual(result.amountAtomic, "1000");
ok(typeof result.settlementRef === "string" && result.settlementRef.startsWith("0x"));
ok(result.confirmations === "2");
console.log("agentledger-settlement: smoke verifyReceipt OK");
