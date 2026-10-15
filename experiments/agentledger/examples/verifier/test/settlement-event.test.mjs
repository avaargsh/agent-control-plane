// SPDX-License-Identifier: Apache-2.0
// Guarantee public event topic constants are derived from the ABI signatures
// rather than guessed or copied from an unverified RPC example.
import test from "node:test";
import assert from "node:assert/strict";
import { keccak256, toBytes } from "viem";
import { ERC3009_EVENT0, TRANSFER_TOPIC } from "../../../tools/settlement.mjs";

test("AuthorizationUsed topic matches EIP-3009 event signature", () => {
  assert.equal(ERC3009_EVENT0, keccak256(toBytes("AuthorizationUsed(address,bytes32)")));
});
test("ERC-20 Transfer topic matches its canonical event signature", () => {
  assert.equal(TRANSFER_TOPIC, keccak256(toBytes("Transfer(address,address,uint256)")));
});
