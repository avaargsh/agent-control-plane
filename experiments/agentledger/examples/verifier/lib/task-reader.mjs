// SPDX-License-Identifier: Apache-2.0
// Read-only Monad Testnet contract inspection; NO signing keys or RPC proxy.
import { createPublicClient, http } from "viem";

export const CHAIN_ID = 10143;
export const RPC = "https://testnet-rpc.monad.xyz";
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const BYTES32 = /^0x[0-9a-fA-F]{64}$/;

export const TASK_ABI = [{
  type: "function",
  name: "getTask",
  stateMutability: "view",
  inputs: [{ name: "taskId", type: "bytes32" }],
  outputs: [{
    name: "task", type: "tuple",
    components: [
      {name: "creator", type: "address"},
      {name: "payer", type: "address"},
      {name: "amount", type: "uint256"},
      {name: "maxBudget", type: "uint256"},
      {name: "descriptionHash", type: "bytes32"},
      {name: "evidenceHash", type: "bytes32"},
      {name: "settlementRef", type: "bytes32"},
      {name: "status", type: "uint8"},
      {name: "createdAt", type: "uint64"},
      {name: "paidAt", type: "uint64"},
      {name: "provenAt", type: "uint64"}
    ]
  }]
}];
const STATUS = ["None", "Created", "Paid", "Proven"];
const check = (condition, message) => { if (!condition) throw Error(message); };

export function validateLookup(contract, taskId) {
  check(typeof contract === "string" && ADDRESS.test(contract), "invalid contract address");
  check(typeof taskId === "string" && BYTES32.test(taskId), "invalid bytes32 task ID");
  return { contract, taskId: taskId.toLowerCase() };
}

export function normalizeTask(taskId, contract, task) {
  check(task && typeof task === "object", "invalid contract result");
  const status = Number(task.status);
  check(Number.isInteger(status) && status >= 1 && status <= 3, "invalid task state");
  const creator = task.creator;
  const payer = task.payer;
  check(ADDRESS.test(creator) && ADDRESS.test(payer), "invalid task addresses");
  for (const field of ["descriptionHash", "evidenceHash", "settlementRef"]) {
    check(BYTES32.test(task[field]), "invalid " + field);
  }
  return {
    network: "Monad Testnet", chainId: CHAIN_ID, contract, taskId,
    creator, payer, status: STATUS[status], statusValue: status,
    amountAtomic: BigInt(task.amount).toString(),
    maxBudgetAtomic: BigInt(task.maxBudget).toString(),
    descriptionHash: task.descriptionHash.toLowerCase(),
    evidenceHash: task.evidenceHash.toLowerCase(),
    settlementRef: task.settlementRef.toLowerCase(),
    createdAt: BigInt(task.createdAt).toString(),
    paidAt: BigInt(task.paidAt).toString(),
    provenAt: BigInt(task.provenAt).toString(),
    caveat: "Payment is a trusted recorder attestation; evidence hash equality is not independent execution proof."
  };
}

// Injectable client enables hermetic tests without contacting a real RPC.
export async function readTask(client, contract, taskId) {
  const input = validateLookup(contract, taskId);
  check(await client.getChainId() === CHAIN_ID, "RPC connected to wrong chain (expected 10143)");
  const code = await client.getBytecode({ address: input.contract });
  check(typeof code === "string" && code !== "0x" && code.length > 2, "no contract bytecode at address");
  const task = await client.readContract({
    address: input.contract,
    abi: TASK_ABI,
    functionName: "getTask",
    args: [input.taskId],
  });
  return normalizeTask(input.taskId, input.contract, task);
}

export function makeClient(rpc = RPC) {
  const url = new URL(rpc);
  check(url.protocol === "https:" || (url.protocol === "http:" && ["localhost", "127.0.0.1", "::1"].includes(url.hostname)),
    "RPC must use HTTPS or local loopback");
  // Caller-provided URLs are only accepted via process environment, never HTTP query params.
  return createPublicClient({ transport: http(rpc, { timeout: 10000, retryCount: 0 }) });
}
