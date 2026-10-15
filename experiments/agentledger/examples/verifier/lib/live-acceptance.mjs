// SPDX-License-Identifier: Apache-2.0
/**
 * Phase-0 public Monad TESTNET acceptance: strictly read-only, no signer.
 * Reports observed chain facts, NEVER finality, execution ownership or refund.
 */
import { parseAbiItem, parseEventLogs } from "viem";
import { makeClient, readTask } from "./task-reader.mjs";
import { inspectPhase0 } from "./phase0-inspect.mjs";

const PINNED_RPC = "https://testnet-rpc.monad.xyz/";
const HASH = /^0x[0-9a-fA-F]{64}$/;
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const check = (ok, msg) => { if (!ok) throw Error(msg); };
const paymentEvent = parseAbiItem(
  "event PaymentAttested(bytes32 indexed taskId, address indexed payer, uint256 amount, bytes32 indexed settlementRef)"
);
const anchorEvent = parseAbiItem(
  "event EvidenceAnchored(bytes32 indexed taskId, bytes32 indexed evidenceHash, address indexed submitter)"
);

export function assertPublicTestnetRpc(raw) {
  check(typeof raw === "string" && raw.length > 0, "Monad Testnet RPC required");
  const target = new URL(raw);
  // Exact HTTPS official RPC only for a LIVE claim. Other valid providers can
  // be used for independent rechecks via the ordinary read-only verifier.
  check(target.href === PINNED_RPC, "live acceptance requires pinned official HTTPS Monad Testnet RPC");
  return target.href;
}
export function validateAcceptanceRequest(request) {
  check(request?.schemaVersion === "agentledger.phase0.live-acceptance.v1",
    "unsupported live acceptance request");
  check(request.network === "eip155:10143", "wrong acceptance network");
  check(typeof request.ledger === "string" && ADDRESS.test(request.ledger),
    "ledger address required");
  check(typeof request.taskId === "string" && HASH.test(request.taskId),
    "task ID required");
  check(request.attempt === "Reserved", "real payment attempt must be reserved; cannot claim NotStarted");
  const p = request.payment;
  check(p && p.approval && p.pendingReceipt && ADDRESS.test(p.expectedPayTo || ""),
    "signed payer approval, original payment notice and exact payee required");
  check(request.evidence?.file && request.evidence?.inputFile && request.evidence?.outputFile,
    "canonical Evidence JSON and original input/output paths required");
  check(HASH.test(request.recorderTransaction || ""), "recorder transaction hash required");
  check(HASH.test(request.anchorTransaction || ""), "Evidence anchor transaction hash required");
  return request;
}
function equalAddress(a,b) {
  return typeof a === "string" && typeof b === "string" && a.toLowerCase() === b.toLowerCase();
}
function equalHash(a,b) {
  return typeof a === "string" && typeof b === "string" && a.toLowerCase() === b.toLowerCase();
}
export async function verifyLedgerEvent(client,{ledger,txHash,eventName,eventAbi,checkArgs}) {
  const hash = txHash.toLowerCase();
  const receipt = await client.getTransactionReceipt({hash});
  check(receipt && receipt.status === "success", eventName + " transaction not successful");
  check(equalHash(receipt.transactionHash,hash), eventName + " transaction hash mismatch");
  check(typeof receipt.blockNumber === "bigint", eventName + " block number missing");
  check(HASH.test(receipt.blockHash || ""), eventName + " block hash missing");
  const canonical = await client.getBlock({blockNumber:receipt.blockNumber});
  check(canonical && equalHash(canonical.hash,receipt.blockHash),
    eventName + " receipt block is no longer canonical");
  check(canonical.number === receipt.blockNumber, eventName + " block height mismatch");
  const contractLogs = receipt.logs.filter(log => equalAddress(log.address,ledger));
  const events = parseEventLogs({
    abi:[eventAbi],logs:contractLogs,eventName,strict:true,
  });
  check(events.length === 1, "expected exactly one "+eventName+" event");
  checkArgs(events[0].args);
  return {
    transaction:hash,blockNumber:receipt.blockNumber.toString(),
    blockHash:receipt.blockHash.toLowerCase(),
    canonicalBlockCheckedAtObservation:true,
    event:eventName,
  };
}
/**
 * Verification calls are independent reads; no ACP/Agent execution mutation,
 * payment signing, contract writes, or wallet key use.
 *
 * 'inspect' injection is for hermetic tests only. The CLI never injects
 * a fake inspector and pins the real RPC before any claim is produced.
 */
export async function runLiveAcceptance(request, deps={}) {
  validateAcceptanceRequest(request);
  const rpc=assertPublicTestnetRpc(deps.rpc || process.env.MONAD_RPC_URL || PINNED_RPC);
  const client=deps.client || makeClient(rpc);
  check(await client.getChainId()===10143,"RPC not on Monad Testnet");
  const inspected=await (deps.inspect || inspectPhase0)(request,{chain:client});
  check(inspected.schemaVersion==="agentledger.phase0.status.v1","unexpected Phase0 schema");
  check(inspected.state==="EvidenceAnchored" &&
    inspected.payment?.eventIndependentlyChecked === true &&
    inspected.evidence?.bytesIndependentlyChecked === true &&
    inspected.retryPolicy?.chargeAgain === false,
    "Phase0 lacks independently verified payment and Evidence facts; PaymentUnknown/no recharge");
  const payment=inspected.inspection?.paymentWitness;
  check(payment?.canonicalBlockCheckedAtObservation === true &&
    HASH.test(payment.blockHash || "") &&
    HASH.test(payment.transaction || "") &&
    equalHash(payment.transaction,inspected.payment.transaction),
    "verified USDC canonical block witness unavailable");
  const task=await (deps.readTask || readTask)(client,request.ledger,request.taskId);
  check(task.status==="Proven" &&
    equalHash(task.taskId,request.taskId) &&
    equalHash(task.evidenceHash,inspected.evidence.onchainHash) &&
    equalHash(task.settlementRef,inspected.payment.settlementRef),
    "re-read onchain task changed during acceptance; reconcile first");

  const recorderAddress=await client.readContract({
    address:request.ledger,
    abi:[parseAbiItem("function paymentRecorder() view returns (address)")],
    functionName:"paymentRecorder",
  });
  check(ADDRESS.test(recorderAddress),"trusted recorder address unavailable");
  const recorder=await verifyLedgerEvent(client,{
    ledger:request.ledger,txHash:request.recorderTransaction,
    eventName:"PaymentAttested",eventAbi:paymentEvent,
    checkArgs:(args)=>{
      check(equalHash(args.taskId,request.taskId),"recorder task ID mismatch");
      check(equalAddress(args.payer,task.payer),"recorder payer mismatch");
      check(args.amount===BigInt(task.amountAtomic),"recorder amount mismatch");
      check(equalHash(args.settlementRef,task.settlementRef),"recorder settlementRef mismatch");
    },
  });
  const anchor=await verifyLedgerEvent(client,{
    ledger:request.ledger,txHash:request.anchorTransaction,
    eventName:"EvidenceAnchored",eventAbi:anchorEvent,
    checkArgs:args=>{
      check(equalHash(args.taskId,request.taskId),"Evidence task ID mismatch");
      check(equalHash(args.evidenceHash,task.evidenceHash),"Evidence anchor hash mismatch");
      check(equalAddress(args.submitter,task.creator) ||
        equalAddress(args.submitter,recorderAddress),
        "Evidence submitted by unapproved actor");
    },
  });
  return {
    schemaVersion:"agentledger.phase0.live-observation.v1",
    result:"PASS_OBSERVED_NOT_FINALITY",
    network:"MONAD_TESTNET_ONLY",
    chainId:10143,
    rpcPinnedToOfficialHTTPS:true,
    taskId:task.taskId,
    ledger:request.ledger,
    phase0State:inspected.state,
    payment:{
      transaction:payment.transaction,
      blockHash:payment.blockHash,
      authorizationNonce:inspected.payment.authorizationNonce,
      settlementRef:inspected.payment.settlementRef,
      receiptCanonicalAtObservation:true,
    },
    recorder,
    evidence:{
      transaction:anchor.transaction,
      blockHash:anchor.blockHash,
      evidenceHash:task.evidenceHash,
      bytesIndependentlyChecked:true,
      anchorCanonicalAtObservation:true,
    },
    executionAuthority:"ACP",
    walletKeysUsed:false,contractWritesPerformed:false,
    finalityProven:false,serviceDeliveryProven:false,
    sdkReady:false,
    caveat:"This is a point-in-time independently recheckable Testnet observation, not a signed proof of chain finality or Agent correctness.",
  };
}
