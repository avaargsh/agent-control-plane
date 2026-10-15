// SPDX-License-Identifier: Apache-2.0
// Read-only verifier: viem-backed chain task lookup. No wallet, secret,
// signing, or mutation API.
import { createPublicClient, http } from "viem";
import type { PublicClient } from "viem";

export const MONAD_CHAIN_ID = 10143;

const GET_TASK_ABI = [{
  type: "function", name: "getTask", stateMutability: "view",
  inputs: [{ name: "taskId", type: "bytes32" }],
  outputs: [{
    name: "task", type: "tuple", components: [
      { name: "creator", type: "address" }, { name: "payer", type: "address" },
      { name: "amount", type: "uint256" }, { name: "maxBudget", type: "uint256" },
      { name: "descriptionHash", type: "bytes32" }, { name: "evidenceHash", type: "bytes32" },
      { name: "settlementRef", type: "bytes32" }, { name: "status", type: "uint8" },
      { name: "createdAt", type: "uint64" }, { name: "paidAt", type: "uint64" },
      { name: "provenAt", type: "uint64" },
    ]
  }]
}] as const;

export interface ChainTask {
  status: "Created" | "Paid" | "Proven";
  creator: string;
  payer: string;
  amount: string;
  maxBudget: string;
  descriptionHash: string;
  evidenceHash: string;
  settlementRef: string;
  createdAt: string;
  paidAt: string;
  provenAt: string;
}

function makeClient(rpcUrl: string | undefined): PublicClient {
  const url = rpcUrl ?? "https://testnet-rpc.monad.xyz";
  return createPublicClient({ transport: http(url) } as any);
}

async function readTask(
  client: PublicClient,
  contract: string,
  taskId: string,
): Promise<ChainTask> {
  if (!/^0x[0-9a-fA-F]{40}$/.test(contract)) throw new Error("invalid contract address");
  if (!/^0x[0-9a-fA-F]{64}$/.test(taskId)) throw new Error("invalid taskId");
  const code = await client.getBytecode({ address: contract as `0x${string}` });
  if (!code || code === "0x") throw new Error("ledger contract not deployed");
  const task = await client.readContract({
    address: contract as `0x${string}`,
    abi: GET_TASK_ABI,
    functionName: "getTask",
    args: [taskId as `0x${string}`],
  }) as any;
  const statusMap: Record<number, string> = { 0: "None", 1: "Created", 2: "Paid", 3: "Proven" };
  const status = statusMap[Number(task.status)] ?? "Unknown";
  if (!["Created", "Paid", "Proven"].includes(status)) {
    throw new Error("unexpected chain task status: " + status);
  }
  return {
    status: status as "Created" | "Paid" | "Proven",
    creator: task.creator, payer: task.payer,
    amount: task.amount.toString(), maxBudget: task.maxBudget.toString(),
    descriptionHash: task.descriptionHash, evidenceHash: task.evidenceHash,
    settlementRef: task.settlementRef,
    createdAt: task.createdAt.toString(), paidAt: task.paidAt.toString(),
    provenAt: task.provenAt.toString(),
  };
}

export { makeClient, readTask };
