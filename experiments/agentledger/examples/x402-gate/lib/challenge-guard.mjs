// SPDX-License-Identifier: Apache-2.0
// Fail-closed pre-charge gate for one static Monad Testnet x402 task route.
// Payment request EIP-3009 and AgentLedger TaskPaymentApproval EIP-712 signatures
// are independently verified before any settlement code can be reached.
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  createPublicClient, hashTypedData, http, verifyTypedData,
} from "viem";
import {
  buildApprovalData, assertAddress, assertHex32, assertUintString,
  MONAD_CHAIN_ID, MONAD_NETWORK, USDC_CONTRACT,
} from "../../../lib/approval-format.mjs";

export const AMOUNT_ATOMIC = "1000";
export const PAYMENT_HEADER = "payment-signature";
export const APPROVAL_HEADER = "x-agentledger-approval";
const SCRIPT = fileURLToPath(new URL("../../../tools/charge-attempt-journal.py", import.meta.url));
const DB = fileURLToPath(new URL("../../../receipts/charge-attempts.db", import.meta.url));
const GET_TASK_ABI = [{
  type: "function", name: "getTask", stateMutability: "view",
  inputs: [{ name:"taskId", type:"bytes32" }],
  outputs: [{
    name:"task", type:"tuple", components: [
      {name:"creator",type:"address"}, {name:"payer",type:"address"},
      {name:"amount",type:"uint256"}, {name:"maxBudget",type:"uint256"},
      {name:"descriptionHash",type:"bytes32"}, {name:"evidenceHash",type:"bytes32"},
      {name:"settlementRef",type:"bytes32"}, {name:"status",type:"uint8"},
      {name:"createdAt",type:"uint64"}, {name:"paidAt",type:"uint64"},
      {name:"provenAt",type:"uint64"}
    ]
  }]
}];
export const TRANSFER_TYPES = {
  TransferWithAuthorization: [
    { name:"from", type:"address" }, { name:"to", type:"address" },
    { name:"value", type:"uint256" }, { name:"validAfter", type:"uint256" },
    { name:"validBefore", type:"uint256" }, { name:"nonce", type:"bytes32" },
  ]
};
const check = (yes, message) => { if (!yes) throw Error(message); };

export function parseHeader(raw, label) {
  check(typeof raw === "string" && raw.length > 0 && raw.length <= 8192, label + " missing or too large");
  check(/^[A-Za-z0-9+/]+={0,2}$/.test(raw) && raw.length % 4 !== 1, label + " invalid base64");
  const decoded = Buffer.from(raw, "base64");
  check(decoded.toString("base64").replace(/=+$/,"") === raw.replace(/=+$/,""), label + " noncanonical base64");
  const json = JSON.parse(new TextDecoder("utf8", {fatal:true}).decode(decoded));
  check(json !== null && typeof json === "object" && !Array.isArray(json), label + " must be an object");
  return json;
}

export function guardConfig(config) {
  const ledger = assertAddress(config.ledger, "ledger address");
  const payTo = assertAddress(config.payTo, "payee");
  const taskId = assertHex32(config.taskId, "task");
  const origin = new URL(config.publicOrigin);
  check(origin.pathname === "/" && !origin.search && !origin.hash && !origin.username &&
    !origin.password, "public origin cannot include path, query, userinfo");
  check(origin.protocol === "https:" ||
    (origin.protocol === "http:" && ["127.0.0.1","localhost"].includes(origin.hostname)), "public origin must be HTTPS or loopback");
  const resourcePath = "/tasks/" + taskId + "/run";
  return {ledger, payTo, taskId, origin: origin.origin,
    resourcePath, resourceURL: origin.origin + resourcePath };
}

export async function readCreatedTask(ledger, taskId, rpcUrl) {
  const url = new URL(rpcUrl);
  check(url.protocol === "https:" ||
    (url.protocol === "http:" && ["127.0.0.1","localhost"].includes(url.hostname)), "invalid RPC URL");
  const client = createPublicClient({transport:http(rpcUrl,{timeout:10000,retryCount:0})});
  check(await client.getChainId() === MONAD_CHAIN_ID, "wrong RPC chain");
  const code = await client.getBytecode({address:ledger});
  check(code && code !== "0x", "ledger contract not deployed");
  const task = await client.readContract({
    address:ledger,abi:GET_TASK_ABI,functionName:"getTask",args:[taskId]
  });
  return {
    status: Number(task.status) === 1 ? "Created" : "Other",
    taskId, descriptionHash:task.descriptionHash,
    maxBudgetAtomic:task.maxBudget.toString(),
  };
}

export async function verifyTaskQuote(approval, config, task, nowSeconds) {
  const {signature, ...quote} = approval || {};
  check(typeof signature === "string" && /^0x[0-9a-fA-F]{130}$/.test(signature), "missing EOA quote signature");
  const typed = buildApprovalData(quote);
  check(typed.domain.verifyingContract === config.ledger, "quote ledger mismatch");
  check(typed.message.taskId === config.taskId, "quote task mismatch");
  check(typed.message.payTo === config.payTo, "quote payee mismatch");
  check(typed.message.amountAtomic.toString() === AMOUNT_ATOMIC, "quote amount mismatch");
  check(typed.message.expiresAt > nowSeconds, "quote expired");
  check(typed.message.expiresAt <= nowSeconds + 86400n, "quote lifetime exceeds 24h");
  check(task && task.status === "Created", "chain task not Created");
  check(assertHex32(task.taskId, "chain task") === typed.message.taskId, "chain task mismatch");
  check(assertHex32(task.descriptionHash, "chain description") === typed.message.descriptionHash, "chain description mismatch");
  check(typed.message.amountAtomic <= BigInt(task.maxBudgetAtomic) ||
    BigInt(task.maxBudgetAtomic) === 0n, "task budget exceeded");
  check(await verifyTypedData({
    address:typed.address, domain:typed.domain,types:typed.types,
    primaryType:typed.primaryType,message:typed.message,signature
  }), "payer task signature invalid");
  return typed;
}

export async function verifyX402Payload(payment, approved, config, nowSeconds) {
  check(payment?.x402Version === 2, "unsupported x402 version");
  check(payment.resource?.url === config.resourceURL, "x402 resource URL mismatch");
  const accepted = payment.accepted;
  check(accepted && accepted.scheme === "exact" && accepted.network === MONAD_NETWORK,
    "x402 network or scheme mismatch");
  check(assertAddress(accepted.asset, "payment asset") === USDC_CONTRACT.toLowerCase(),
    "x402 token mismatch");
  check(assertAddress(accepted.payTo, "payment recipient") === config.payTo,
    "x402 recipient mismatch");
  check(assertUintString(accepted.amount, "payment amount") === approved.message.amountAtomic,
    "x402 amount mismatch");
  const auth = payment.payload?.authorization;
  const signature = payment.payload?.signature;
  check(auth && typeof auth === "object", "missing EIP-3009 authorization");
  check(typeof signature === "string" && /^0x[0-9a-fA-F]{130}$/.test(signature), "invalid EIP-3009 signature");
  const from = assertAddress(auth.from, "payment from");
  const to = assertAddress(auth.to, "payment to");
  const nonce = assertHex32(auth.nonce, "payment nonce");
  const value = assertUintString(auth.value, "payment value");
  const validAfter = assertUintString(auth.validAfter, "validAfter");
  const validBefore = assertUintString(auth.validBefore, "validBefore");
  check(from === approved.address && to === config.payTo && value === approved.message.amountAtomic,
    "EIP-3009 amount, payer or recipient mismatch");
  check(validAfter <= nowSeconds && validBefore > nowSeconds, "EIP-3009 outside validity window");
  check(validBefore <= approved.message.expiresAt, "EIP-3009 exceeds task approval expiry");
  const valid = await verifyTypedData({
    address:approved.address,
    domain:{name:"USDC",version:"2",chainId:MONAD_CHAIN_ID,verifyingContract:USDC_CONTRACT},
    primaryType:"TransferWithAuthorization",types:TRANSFER_TYPES,
    message:{from,to,value,validAfter,validBefore,nonce},
    signature,
  });
  check(valid, "EIP-3009 payment signature invalid");
  return {nonce};
}

export function reservePaymentAttempt(candidate, opts={}) {
  const job=spawnSync(opts.python||"python3",[SCRIPT,"reserve"],{
    input:JSON.stringify(candidate),encoding:"utf8",maxBuffer:65536,timeout:15000,
    env:{...process.env,AGENTLEDGER_CHARGE_DB:opts.dbPath||
      process.env.AGENTLEDGER_CHARGE_DB||DB}
  });
  if(job.error) throw job.error;
  if(job.status !== 0) throw Error("precharge reservation blocked or unavailable; reconcile before retry");
  const result=JSON.parse(job.stdout);
  check(result.result==="RESERVED_FIRST_ATTEMPT","precharge not reserved");
  return result;
}

/**
 * Run before paymentMiddleware. For an unpaid request, verify user intent
 * and allow the x402 middleware to generate the 402. For a paid retry,
 * verify BOTH wallet signatures and transaction parameters then reserve
 * the charge attempt ONCE before allowing facilitator settlement.
 */
export async function authorizeHttpRequest(headers, config, deps={}) {
  const now=deps.nowSeconds ?? BigInt(Math.floor(Date.now()/1000));
  const rawApproval=headers[APPROVAL_HEADER];
  const quote=parseHeader(rawApproval, "task approval");
  const task=await (deps.readTask || readCreatedTask)(
    config.ledger,config.taskId,deps.rpcUrl||process.env.MONAD_RPC_URL||"https://testnet-rpc.monad.xyz");
  const approved=await (deps.verifyQuote || verifyTaskQuote)(quote,config,task,now);
  if(!headers[PAYMENT_HEADER]) return {phase:"CHALLENGE_ALLOWED",taskId:config.taskId};
  const payment=parseHeader(headers[PAYMENT_HEADER],"x402 payload");
  const verified=await (deps.verifyPayload || verifyX402Payload)(payment,approved,config,now);
  const quoteDigest=hashTypedData({
    domain:approved.domain,types:approved.types,primaryType:approved.primaryType,
    message:approved.message
  }).toLowerCase();
  const payloadDigest="0x"+createHash("sha256").update(headers[PAYMENT_HEADER],"utf8").digest("hex");
  const reservation=(deps.reserve || reservePaymentAttempt)({
    schemaVersion:"agentledger.precharge.v1",
    ledger:config.ledger,taskId:config.taskId,payer:approved.address,
    approvalNonce:approved.message.nonce,paymentNonce:verified.nonce,
    quoteDigest,payloadDigest,
  });
  return {phase:"ONE_PAYMENT_ATTEMPT_RESERVED",taskId:config.taskId,reservation};
}
