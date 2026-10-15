// SPDX-License-Identifier: Apache-2.0
// Fail-closed pre-charge gate for one static Monad Testnet x402 task route.
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  createPublicClient, hashTypedData, http, verifyTypedData,
} from "viem";
import {
  buildApprovalData, assertAddress, assertHex32, assertUintString,
  MONAD_CHAIN_ID, MONAD_NETWORK, USDC_CONTRACT,
  type Eip712ApprovalData,
} from "@agentledger/core";

const AMOUNT_ATOMIC = "1000";
const PAYMENT_HEADER = "payment-signature";
const APPROVAL_HEADER = "x-agentledger-approval";

const SCRIPT = fileURLToPath(
  new URL("../../../agentledger-journal-py/src/agentledger_journal/charge_attempt.py", import.meta.url),
);
const DB = fileURLToPath(
  new URL("../../../agentledger-journal-py/receipts/charge-attempts.db", import.meta.url),
);

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

const TRANSFER_TYPES = {
  TransferWithAuthorization: [
    { name: "from", type: "address" }, { name: "to", type: "address" },
    { name: "value", type: "uint256" }, { name: "validAfter", type: "uint256" },
    { name: "validBefore", type: "uint256" }, { name: "nonce", type: "bytes32" },
  ],
} as const;

const check = (yes: boolean, message: string): void => { if (!yes) throw Error(message); };

function parseHeader(raw: unknown, label: string): Record<string, unknown> {
  check(typeof raw === "string" && raw.length > 0 && raw.length <= 8192, label + " missing or too large");
  const s = raw as string;
  check(/^[A-Za-z0-9+/]+={0,2}$/.test(s) && s.length % 4 !== 1, label + " invalid base64");
  const decoded = Buffer.from(s, "base64");
  check(decoded.toString("base64").replace(/=+$/, "") === s.replace(/=+$/, ""), label + " noncanonical base64");
  const json = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(decoded));
  check(json !== null && typeof json === "object" && !Array.isArray(json), label + " must be an object");
  return json as Record<string, unknown>;
}

interface GuardConfig {
  ledger: string;
  payTo: string;
  taskId: string;
  origin: string;
  resourcePath: string;
  resourceURL: string;
}

function guardConfig(config: { ledger?: string; payTo?: string; taskId?: string; publicOrigin?: string }): GuardConfig {
  const ledger = assertAddress(config.ledger, "ledger address");
  const payTo = assertAddress(config.payTo, "payee");
  const taskId = assertHex32(config.taskId, "task");
  const origin = new URL(config.publicOrigin ?? "http://127.0.0.1:4021");
  check(origin.pathname === "/" && !origin.search && !origin.hash && !origin.username &&
    !origin.password, "public origin cannot include path, query, userinfo");
  check(origin.protocol === "https:" ||
    (origin.protocol === "http:" && Boolean(["127.0.0.1", "localhost"].includes(origin.hostname))),
    "public origin must be HTTPS or loopback");
  const resourcePath = "/tasks/" + taskId + "/run";
  return { ledger, payTo, taskId, origin: origin.origin,
    resourcePath, resourceURL: origin.origin + resourcePath };
}

interface CreatedTask {
  status: "Created" | "Other";
  taskId: string;
  descriptionHash: string;
  maxBudgetAtomic: string;
}

async function readCreatedTask(ledger: string, taskId: string, rpcUrl: string): Promise<CreatedTask> {
  const url = new URL(rpcUrl);
  check(url.protocol === "https:" ||
    (url.protocol === "http:" && ["127.0.0.1", "localhost"].includes(url.hostname)), "invalid RPC URL");
  const client = createPublicClient({ transport: http(rpcUrl) } as any);
  check((await client.getChainId()) === MONAD_CHAIN_ID, "wrong RPC chain");
  const code = await client.getBytecode({ address: ledger as `0x${string}` });
  check(Boolean(code && code !== "0x"), "ledger contract not deployed");
  const task = await client.readContract({
    address: ledger as `0x${string}`,
    abi: GET_TASK_ABI,
    functionName: "getTask",
    args: [taskId as `0x${string}`],
  }) as any;
  return {
    status: Number(task.status) === 1 ? "Created" : "Other",
    taskId, descriptionHash: task.descriptionHash,
    maxBudgetAtomic: task.maxBudget.toString(),
  };
}

interface TypedApproval extends Eip712ApprovalData {
  message: Record<string, unknown> & {
    taskId: string;
    descriptionHash: string;
    token: string;
    payTo: string;
    amountAtomic: bigint;
    expiresAt: bigint;
    nonce: string;
  };
}

async function verifyTaskQuote(
  approval: Record<string, unknown>,
  config: GuardConfig,
  task: CreatedTask,
  nowSeconds: bigint,
): Promise<TypedApproval> {
  const { signature, ...quote } = approval as { signature?: unknown; [key: string]: unknown };
  check(typeof signature === "string" && /^0x[0-9a-fA-F]{130}$/.test(signature), "missing EOA quote signature");
  const typed = buildApprovalData(quote) as TypedApproval;
  check(typed.domain.verifyingContract === config.ledger, "quote ledger mismatch");
  check(typed.message.taskId === config.taskId, "quote task mismatch");
  check(typed.message.payTo === config.payTo, "quote payee mismatch");
  check(typed.message.amountAtomic.toString() === AMOUNT_ATOMIC, "quote amount mismatch");
  check(typed.message.expiresAt > nowSeconds, "quote expired");
  check(typed.message.expiresAt <= nowSeconds + 86400n, "quote lifetime exceeds 24h");
  check(task && task.status === "Created", "chain task not Created");
  check(task.taskId === typed.message.taskId, "chain task mismatch");
  check(task.descriptionHash === typed.message.descriptionHash, "chain description mismatch");
  check(typed.message.amountAtomic <= BigInt(task.maxBudgetAtomic) ||
    BigInt(task.maxBudgetAtomic) === 0n, "task budget exceeded");
  check(await verifyTypedData({
    address: typed.address as `0x${string}`,
    domain: typed.domain as any,
    types: typed.types as any,
    primaryType: typed.primaryType,
    message: typed.message as any,
    signature: signature as `0x${string}`,
  }) as boolean, "payer task signature invalid");
  return typed;
}

interface X402Verified {
  nonce: string;
}

async function verifyX402Payload(
  payment: Record<string, unknown>,
  approved: TypedApproval,
  config: GuardConfig,
  nowSeconds: bigint,
): Promise<X402Verified> {
  check((payment as { x402Version?: unknown }).x402Version === 2, "unsupported x402 version");
  const accepted = (payment as { accepted?: unknown }).accepted;
  check(Boolean(accepted && (accepted as { scheme?: unknown }).scheme === "exact") &&
    (accepted as { network?: unknown }).network === MONAD_NETWORK,
    "x402 network or scheme mismatch");
  check(assertAddress((accepted as { asset?: unknown }).asset, "payment asset") === USDC_CONTRACT.toLowerCase(),
    "x402 token mismatch");
  check(assertAddress((accepted as { payTo?: unknown }).payTo, "payment recipient") === config.payTo,
    "x402 recipient mismatch");
  check((accepted as { amount?: unknown }).amount === approved.message.amountAtomic,
    "x402 amount mismatch");
  check(String(((accepted as { extra?: { assetTransferMethod?: unknown } }).extra?.assetTransferMethod ?? "eip3009")) === "eip3009",
    "unsupported x402 asset transfer method");
  const auth = (payment as { payload?: { authorization?: unknown } }).payload?.authorization;
  const signature = (payment as { payload?: { signature?: unknown } }).payload?.signature;
  check(Boolean(auth && typeof auth === "object"), "missing EIP-3009 authorization");
  check(typeof signature === "string" && /^0x[0-9a-fA-F]{130}$/.test(signature), "invalid EIP-3009 signature");
  const from = assertAddress((auth as { from?: unknown }).from, "payment from");
  const to = assertAddress((auth as { to?: unknown }).to, "payment to");
  const nonce = assertHex32((auth as { nonce?: unknown }).nonce, "payment nonce");
  const value = assertUintString((auth as { value?: unknown }).value, "payment value");
  const validAfter = assertUintString((auth as { validAfter?: unknown }).validAfter, "validAfter");
  const validBefore = assertUintString((auth as { validBefore?: unknown }).validBefore, "validBefore");
  check(from === approved.address && to === config.payTo && value === approved.message.amountAtomic,
    "EIP-3009 amount, payer or recipient mismatch");
  check(validAfter <= nowSeconds && validBefore > nowSeconds, "EIP-3009 outside validity window");
  check(validBefore <= approved.message.expiresAt, "EIP-3009 exceeds task approval expiry");
  const valid = await verifyTypedData({
    address: from,
    domain: { name: "USDC", version: "2", chainId: MONAD_CHAIN_ID, verifyingContract: USDC_CONTRACT } as any,
    primaryType: "TransferWithAuthorization",
    types: TRANSFER_TYPES as any,
    message: { from, to, value, validAfter, validBefore, nonce },
    signature,
  } as any) as boolean;
  check(Boolean(valid), "EIP-3009 payment signature invalid");
  return { nonce };
}

interface ReserveResult {
  result: string;
  status: string;
}

function reservePaymentAttempt(candidate: Record<string, unknown>, opts: { python?: string; dbPath?: string } = {}): ReserveResult {
  const job = spawnSync(opts.python ?? "python3", [SCRIPT, "reserve"], {
    input: JSON.stringify(candidate), encoding: "utf8", maxBuffer: 65536, timeout: 15000,
    env: { ...process.env, AGENTLEDGER_CHARGE_DB: opts.dbPath ??
      process.env.AGENTLEDGER_CHARGE_DB ?? DB },
  });
  if (job.error) throw job.error;
  if (job.status !== 0) throw Error("precharge reservation blocked or unavailable; reconcile before retry");
  const result = JSON.parse(job.stdout as string) as ReserveResult;
  check(result.result === "RESERVED_FIRST_ATTEMPT", "precharge not reserved");
  return result;
}

interface AuthorizeResult {
  phase: "CHALLENGE_ALLOWED" | "ONE_PAYMENT_ATTEMPT_RESERVED";
  taskId: string;
  reservation?: ReserveResult;
}

interface AuthorizeDeps {
  nowSeconds?: bigint;
  rpcUrl?: string;
  readTask?: (ledger: string, taskId: string, rpcUrl: string) => Promise<CreatedTask>;
  verifyQuote?: (approval: Record<string, unknown>, config: GuardConfig, task: CreatedTask, nowSeconds: bigint) => Promise<TypedApproval>;
  verifyPayload?: (payment: Record<string, unknown>, approved: TypedApproval, config: GuardConfig, nowSeconds: bigint) => Promise<X402Verified>;
  reserve?: (candidate: Record<string, unknown>, opts?: { python?: string; dbPath?: string }) => ReserveResult;
}

async function authorizeHttpRequest(
  headers: Record<string, string>,
  config: GuardConfig,
  deps: AuthorizeDeps = {},
): Promise<AuthorizeResult> {
  const now = deps.nowSeconds ?? BigInt(Math.floor(Date.now() / 1000));
  const rawApproval = headers[APPROVAL_HEADER];
  const quote = parseHeader(rawApproval, "task approval");
  const task = await (deps.readTask ?? readCreatedTask)(
    config.ledger, config.taskId, deps.rpcUrl ?? process.env.MONAD_RPC_URL ?? "https://testnet-rpc.monad.xyz");
  const approved = await (deps.verifyQuote ?? verifyTaskQuote)(quote, config, task, now);
  if (!headers[PAYMENT_HEADER]) {
    return { phase: "CHALLENGE_ALLOWED", taskId: config.taskId };
  }
  const payment = parseHeader(headers[PAYMENT_HEADER], "x402 payload");
  const verified = await (deps.verifyPayload ?? verifyX402Payload)(payment, approved, config, now);
  const quoteDigest = hashTypedData({
    domain: approved.domain, types: approved.types, primaryType: approved.primaryType,
    message: approved.message,
  } as any).toLowerCase();
  const payloadDigest = "0x" + createHash("sha256").update(headers[PAYMENT_HEADER], "utf8").digest("hex");
  const reservation = (deps.reserve ?? reservePaymentAttempt)({
    schemaVersion: "agentledger.precharge.v1",
    ledger: config.ledger, taskId: config.taskId, payer: approved.address,
    approvalNonce: approved.message.nonce, paymentNonce: verified.nonce,
    quoteDigest, payloadDigest,
  });
  return { phase: "ONE_PAYMENT_ATTEMPT_RESERVED", taskId: config.taskId, reservation };
}

export {
  guardConfig, authorizeHttpRequest, parseHeader, readCreatedTask,
  verifyTaskQuote, verifyX402Payload, reservePaymentAttempt,
  type AuthorizeResult, type AuthorizeDeps, type CreatedTask, type TypedApproval, type X402Verified,
  type GuardConfig, type ReserveResult,
  AMOUNT_ATOMIC, PAYMENT_HEADER, APPROVAL_HEADER,
};
