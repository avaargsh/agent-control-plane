// SPDX-License-Identifier: Apache-2.0
/**
 * AgentLedger read-only SDK (private alpha). Public surface intentionally
 * limited to factory + immutable metadata. No wallet, fixture parser,
 * payment retries, write transport or ACP execution authority.
 *
 * Error contract v1: every SDK-thrown error has stable .code / .operation /
 * .retryable / .paymentStatus / .chargeAgain. No URL, signature, or receipt
 * payload is ever appended to a network error message.
 */
import { readTask, makeClient, CHAIN_ID, RPC, validateLookup } from "../../examples/verifier/lib/task-reader.mjs";
import { verifyEvidence } from "../../examples/verifier/public/evidence-browser.mjs";
import { projectPhase0 } from "../../phase0/status.mjs";
import { verifyLive, NETWORK, USDC_CONTRACT } from "../../tools/settlement.mjs";

export const SDK_VERSION="0.2.0-alpha.3";
export const SDK_CAPABILITIES=Object.freeze({
  observe:true,verify:true,reconcile:true,readOnly:true,
  acceptPayment:false,charge:false,sign:false,mutateLedger:false,
  executeAgent:false,manageAcp:false,proveChainFinality:false,
});
const SDK_SCHEMA="agentledger.sdk.read-only.v1";
const NONCE=/^0x[0-9a-fA-F]{64}$/;
const ADDRESS=/^0x[0-9a-fA-F]{40}$/;
const DECIMAL=/^[1-9][0-9]*$/;
const DENY_TRANSPORT="Injected transport/client is forbidden in public SDK; use rpcUrl. No receipt or charge attempted";

function sdkError(code,operation,message,retryable=false) {
  const e=new Error(message);
  e.name="AgentLedgerSDKError";
  e.code=code;
  e.operation=operation;
  e.retryable=retryable; // retry ONLY read-only observation, NEVER charge
  e.paymentStatus="PaymentUnknown";
  e.chargeAgain=false;
  return e;
}
function requireInput(ok,operation,message) {
  if(!ok)throw sdkError("SDK_INVALID_INPUT",operation,message);
}
function safeRpcUrl(value){
  requireInput(typeof value==="string"&&value.length>0&&value.length<=2048,
    "create","rpcUrl must be a non-empty string");
  let url;
  try{url=new URL(value);}catch{throw sdkError("SDK_INVALID_RPC_URL","create","Invalid RPC URL");}
  const local=url.protocol==="http:"&&["localhost","127.0.0.1","[::1]"].includes(url.hostname);
  if((url.protocol!=="https:"&&!local) || url.username || url.password ||
     url.search || url.hash || (url.pathname!=="/" && url.pathname!=="")) {
    throw sdkError("SDK_INVALID_RPC_URL","create",
      "RPC must be HTTPS or local loopback; credentials, query and URL path are forbidden");
  }
  return url.origin;
}
function classifyFailure(error,operation) {
  if(error?.name==="AgentLedgerSDKError")return error;
  const message=String(error?.message??"");
  if(/wrong chain|RPC is not Monad Testnet/i.test(message))
    return sdkError("SDK_CHAIN_MISMATCH",operation,"RPC connected to wrong chain (expected 10143)");
  if(/SHA-256 mismatch|task ID does not match|not Proven|invalid on-chain task/i.test(message))
    return sdkError("SDK_EVIDENCE_MISMATCH",operation,"Evidence SHA-256 mismatch or task anchor unavailable");
  if(/Unexpected or missing Evidence|Invalid (completedAt|inputSha256|outputSha256|executor|modelOrTool|taskId)|Unsupported Evidence|Evidence must/i.test(message))
    return sdkError("SDK_INVALID_EVIDENCE",operation,"Invalid canonical Evidence payload");
  if(operation==="reconcile" &&
    /transaction hash mismatch|transaction reverted|expected exactly one matching|nonce|incorrect network|only official Monad Testnet USDC|confirmation threshold|receipt block is no longer canonical|log block mismatch|log transaction mismatch|x402 .*mismatch/i.test(message))
    return sdkError("SDK_SETTLEMENT_INCONSISTENT",operation,
      "Receipt, USDC transfer, authorization nonce or canonical chain witness inconsistent; PaymentUnknown, do not recharge");
  if(/invalid contract address|invalid bytes32 task ID/i.test(message))
    return sdkError("SDK_INVALID_INPUT",operation,"Invalid Ledger contract or task ID");
  if(/no contract bytecode|invalid task state|invalid contract result/i.test(message))
    return sdkError("SDK_TASK_UNAVAILABLE",operation,"Ledger task unavailable or contract not deployed");
  return sdkError("SDK_OBSERVATION_UNAVAILABLE",operation,
    "Read-only RPC verification unavailable or inconsistent; PaymentUnknown, do not recharge",true);
}
async function observeSafely(operation,callback){
  try{return await callback();}catch(error){throw classifyFailure(error,operation);}
}
function verifyReceiptInput(expected,options){
  const op="reconcile";
  requireInput(expected!==null&&typeof expected==="object"&&!Array.isArray(expected),
    op,"Expected payment receipt descriptor required");
  // Keep a narrow, versioned input contract rather than forwarding
  // attacker-controlled objects / transport overrides into the RPC client.
  const fields=new Set(["network","taskId","transaction","payer","payTo",
    "token","amountAtomic","paymentNonce"]);
  requireInput(Object.keys(expected).every(key=>fields.has(key)),
    op,"Unknown receipt descriptor field");
  requireInput(expected.network===NETWORK,op,"Expected network must be eip155:10143");
  requireInput(NONCE.test(expected.paymentNonce||""),op,
    "EIP-3009 paymentNonce required; PaymentUnknown, do not recharge");
  requireInput(NONCE.test(expected.taskId||"")&&NONCE.test(expected.transaction||""),
    op,"Expected bytes32 task ID and transaction hash");
  requireInput(ADDRESS.test(expected.payer||"")&&ADDRESS.test(expected.payTo||""),
    op,"Expected valid payer and payTo addresses");
  requireInput(expected.token===undefined ||
    (ADDRESS.test(expected.token||"")&&expected.token.toLowerCase()===USDC_CONTRACT.toLowerCase()),
    op,"Only official Monad Testnet USDC is supported");
  requireInput(typeof expected.amountAtomic==="string"&&DECIMAL.test(expected.amountAtomic),
    op,"Expected positive decimal amountAtomic");
  requireInput(options!==null&&typeof options==="object"&&!Array.isArray(options),
    op,"Receipt observation options must be an object");
  requireInput(Object.keys(options).every(k=>k==="minConfirmations"),
    op,"Unknown receipt observation option");
  const minConfirmations=options.minConfirmations??2;
  requireInput(Number.isInteger(minConfirmations)&&minConfirmations>=1&&
    minConfirmations<=1000,op,"Invalid confirmation policy (1..1000)");
  return {minConfirmations};
}
/**
 * Create a read-only verifier. Caller-provided clients, fetch, transports,
 * signers or per-request RPC URLs are forbidden. Only a fixed HTTPS or
 * loopback RPC URL is accepted. No connection is opened at construction.
 */
export function createAgentLedgerVerifier(options={}) {
  if(!options || typeof options!=="object" || Array.isArray(options))
    throw sdkError("SDK_INVALID_OPTIONS","create","SDK options must be an object");
  // Deliberate order: reject injected transport before URL/nonce parsing.
  if(Object.keys(options).some(k=>["client","transport","fetch","request"].includes(k)))
    throw sdkError("SDK_TRANSPORT_FORBIDDEN","create",DENY_TRANSPORT);
  if(Object.keys(options).some(k=>k!=="rpcUrl"))
    throw sdkError("SDK_INVALID_OPTIONS","create","Unknown SDK option");
  const rpcUrl=safeRpcUrl(options.rpcUrl??RPC);
  const chain=makeClient(rpcUrl);
  return Object.freeze({
    sdkVersion:SDK_VERSION,
    capabilities:SDK_CAPABILITIES,
    async observe(contract,taskId){
      return observeSafely("observe",async()=>{
        validateLookup(contract,taskId);
        const task=await readTask(chain,contract,taskId);
        return Object.freeze({
          schemaVersion:SDK_SCHEMA,sdkVersion:SDK_VERSION,source:"RPC_READ_ONLY",
          task,status:projectPhase0({task,attempt:"NotEstablished"}),
          paymentStatus:"PaymentUnknown",paymentVerified:false,
          publicTestnetAcceptance:"BLOCKED_UNLESS_SEPARATELY_REVIEWED",
          finalityProven:false,chargeAgain:false,executionAuthority:"ACP",
        });
      });
    },
    async verify(contract,taskId,evidence,options={}){
      return observeSafely("verify",async()=>{
        validateLookup(contract,taskId);
        requireInput(options!==null&&typeof options==="object"&&!Array.isArray(options),
          "verify","Evidence verification options must be an object");
        requireInput(Object.keys(options).every(k=>k==="inputBytes"||k==="outputBytes"),
          "verify","Unknown Evidence verification option");
        const {inputBytes=null,outputBytes=null}=options;
        for(const [name,value] of [["inputBytes",inputBytes],["outputBytes",outputBytes]]){
          requireInput(value===null||value instanceof ArrayBuffer||ArrayBuffer.isView(value),
            "verify",name+" must be raw bytes");
        }
        const task=await readTask(chain,contract,taskId);
        const matched=await verifyEvidence(evidence,task,inputBytes,outputBytes);
        return Object.freeze({
          ...matched,schemaVersion:SDK_SCHEMA,sdkVersion:SDK_VERSION,
          source:"CHAIN_ANCHOR_PLUS_LOCAL_BYTES",evidenceHash:task.evidenceHash,
          paymentStatus:"PaymentUnknown",paymentVerified:false,
          finalityProven:false,chargeAgain:false,executionAuthority:"ACP",
          publicTestnetAcceptance:"BLOCKED_UNLESS_SEPARATELY_REVIEWED",
        });
      });
    },
    async reconcile(expected,options={}){
      return observeSafely("reconcile",async()=>{
        const validated=verifyReceiptInput(expected,options); // BEFORE any network
        const payment=await verifyLive(expected,rpcUrl,fetch,validated);
        return Object.freeze({
          schemaVersion:SDK_SCHEMA,sdkVersion:SDK_VERSION,source:"USDC_RECEIPT_ONLY",
          paymentEventObserved:true,nonce:payment.paymentNonce,
          transaction:payment.transaction,blockHash:payment.blockHash,
          settlementRef:payment.settlementRef,chainId:CHAIN_ID,
          status:"PaymentUnknown",payerIntentVerified:false,
          prechargeReservationVerified:false,finalityProven:false,
          publicTestnetAcceptance:"BLOCKED_UNLESS_SEPARATELY_REVIEWED",
          chargeAgain:false,executionAuthority:"ACP",
          note:"Read-only Transfer + AuthorizationUsed observation. Signed intent and durable reservation require independent Phase 0 reconciliation.",
        });
      });
    },
  });
}
