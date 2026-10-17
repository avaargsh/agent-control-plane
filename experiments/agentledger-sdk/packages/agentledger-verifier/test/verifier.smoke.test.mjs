// SPDX-License-Identifier: Apache-2.0
import { strictEqual, ok } from "node:assert";
import { makeClient } from "../dist/lib/task-reader.js";

// Just smoke-test imports and the client factory (no network call).
const client = makeClient(undefined);
strictEqual(typeof client, "object");
strictEqual(typeof client.readContract, "function");
console.log("agentledger-verifier: smoke import OK");
