// SPDX-License-Identifier: Apache-2.0
/**
 * Phase-0 public Monad TESTNET acceptance: strictly read-only, no signer.
 * Reports observed chain facts, NEVER finality, execution ownership or refund.
 */
import { decodeFunctionData, parseAbiItem, parseEventLogs } from "viem";
import { makeClient, readTask } from "./task-reader.mjs";
import { inspectPhase0 } from "./phase0-inspect.mjs";
import { verifyReviewedDeployment } from "./reviewed-deployment.mjs";

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
const recordPaymentMethod=parseAbiItem(
  "function recordPayment(bytes32 taskId, address payer, uint256 amount, bytes32 settlementRef)"
);
const submitEvidenceMethod=parseAbiItem(
  "function submitEvidence(bytes32 taskId, bytes32 evidenceHash)"
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
function strictIndex(value,label) {
  check(Number.isSafeInteger(value) && value >= 0, label + " transaction index missing or invalid");
  return value;
}
function strictHeight(value,label) {
  check(typeof value === "string" && /^(0|[1-9][0-9]*)$/.test(value), label + " block height invalid");
  return BigInt(value);
}
export function assertOrderedEvents(payment,recorder,anchor) {
  const witnesses=[
    ["USDC",payment],["Recorder",recorder],["Evidence",anchor],
  ];
  const observedHashes=new Set();
  const positions=witnesses.map(([label,witness])=>{
    check(HASH.test(witness?.transaction || ""), label + " transaction hash missing");
    const hash=witness.transaction.toLowerCase();
    check(!observedHashes.has(hash),"payment/recorder/evidence transactions must be distinct");
    observedHashes.add(hash);
    check(HASH.test(witness.blockHash || ""), label + " block hash missing");
    return {label,height:strictHeight(witness.blockNumber,label),
      index:strictIndex(witness.transactionIndex,label)};
  });
  for(let n=1;n<positions.length;n++) {
    const before=positions[n-1],after=positions[n];
    check(after.height>before.height ||
      (after.height===before.height && after.index>before.index),
    "onchain event chronology mismatch: "+before.label+" must precede "+after.label);
  }
  return true;
}
/**
 * Validate the actual outer EVM transaction, not only logs emitted by a
 * contract in its receipt. Phase 0 supports direct authorized calls only;
 * multisig wrapper transactions need a separately reviewed adapter.
 */
export async function verifyDirectLedgerCall(client,{ledger,txHash,caller,method,args}) {
  const tx=await client.getTransaction({hash:txHash.toLowerCase()});
  check(tx && equalHash(tx.hash,txHash), "ledger call transaction unavailable or hash mismatch");
  check(equalAddress(tx.to,ledger), "ledger call target contract mismatch");
  check(equalAddress(tx.from,caller), "ledger call initiator is not authorized signer");
  let decoded;
  try {
    decoded=decodeFunctionData({abi:[method],data:tx.input});
  } catch (_) {
    throw Error("ledger call function selector/parameters invalid");
  }
  check(decoded.functionName===method.name &&
    decoded.args?.length===args.length,"ledger call function mismatch");
  for(let i=0;i<args.length;i++){
    const actual=decoded.args[i],expected=args[i];
    check(typeof expected==="bigint" ? actual===expected :
      (typeof expected==="string"&&typeof actual==="string"&&
        actual.toLowerCase()===expected.toLowerCase()),
    "ledger call parameter mismatch at index "+i);
  }
  return {caller:caller.toLowerCase(),target:ledger.toLowerCase(),
    functionName:decoded.functionName,transaction:txHash.toLowerCase()};
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
  strictIndex(receipt.transactionIndex,eventName);
  check(Array.isArray(receipt.logs),eventName+" receipt logs absent");
  // Never accept an event that is reported in a different transaction,
  // block, or explicitly removed by the RPC provider.
  const contractLogs = receipt.logs.filter(log =>
    equalAddress(log.address,ledger) &&
    log.removed !== true &&
    equalHash(log.transactionHash,hash) &&
    equalHash(log.blockHash,receipt.blockHash));
  check(receipt.logs.filter(log=>equalAddress(log?.address,ledger))
    .length === contractLogs.length,eventName+" contract logs inconsistent with receipt");
  const events = parseEventLogs({
    abi:[eventAbi],logs:contractLogs,eventName,strict:true,
  });
  check(events.length === 1, "expected exactly one "+eventName+" event");
  checkArgs(events[0].args);
  return {
    transaction:hash,blockNumber:receipt.blockNumber.toString(),
    transactionIndex:receipt.transactionIndex,
    blockHash:receipt.blockHash.toLowerCase(),
    canonicalBlockCheckedAtObservation:true,
    event:eventName,
    ...(eventName==="EvidenceAnchored"?{submitter:events[0].args.submitter}:{}),
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
  // Check deployment provenance BEFORE trusting even well-formed events.
  // A caller-provided Ledger address cannot approve itself: the production
  // verifier reads the committed, PR-reviewed allowlist (default deny).
  const recorderAddress=await client.readContract({
    address:request.ledger,
    abi:[parseAbiItem("function paymentRecorder() view returns (address)")],
    functionName:"paymentRecorder",
  });
  check(ADDRESS.test(recorderAddress),"trusted Recorder address unavailable");
  const reviewed=await (deps.verifyDeployment||verifyReviewedDeployment)(
    client,request.ledger,recorderAddress
  );
  check(reviewed?.schemaVersion==="agentledger.reviewed-deployment-witness.v1" &&
    equalAddress(reviewed.address,request.ledger) &&
    equalAddress(reviewed.recorder,recorderAddress) &&
    HASH.test(reviewed.blockHash||"") &&
    HASH.test(reviewed.runtimeKeccak||""),
    "PR-reviewed Ledger deployment evidence unavailable");
  const phase0Request={
    schemaVersion:"agentledger.phase0.inspect.v1",
    ledger:request.ledger,
    taskId:request.taskId.toLowerCase(),
    attempt:"Reserved",
    payment:request.payment,
    evidence:request.evidence,
  };
  const inspected=await (deps.inspect || inspectPhase0)(phase0Request,{chain:client});
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
  strictHeight(payment.blockNumber,"USDC");
  strictIndex(payment.transactionIndex,"USDC");
  const task=await (deps.readTask || readTask)(client,request.ledger,request.taskId);
  check(task.status==="Proven" &&
    equalHash(task.taskId,request.taskId) &&
    equalHash(task.evidenceHash,inspected.evidence.onchainHash) &&
    equalHash(task.settlementRef,inspected.payment.settlementRef),
    "re-read onchain task changed during acceptance; reconcile first");

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
  const recorderCall=await verifyDirectLedgerCall(client,{
    ledger:request.ledger,txHash:request.recorderTransaction,
    caller:recorderAddress,method:recordPaymentMethod,
    args:[task.taskId,task.payer,BigInt(task.amountAtomic),task.settlementRef],
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
  // Caller and event submitter MUST refer to the same authorized actor.
  const anchorCall=await verifyDirectLedgerCall(client,{
    ledger:request.ledger,txHash:request.anchorTransaction,
    caller:anchor.submitter,method:submitEvidenceMethod,
    args:[task.taskId,task.evidenceHash],
  });
  assertOrderedEvents(payment,recorder,anchor);
  // A reorg can occur between the initial payment receipt read and the
  // later Recorder/Evidence queries. Re-read all three canonical headers at
  // the end of acceptance to avoid reporting a stale mixed-chain snapshot.
  const witnesses=[payment,recorder,anchor,reviewed];
  for(const [i,witness] of witnesses.entries()){
    const block=await client.getBlock({blockNumber:strictHeight(witness.blockNumber,"witness")});
    check(block && block.number===BigInt(witness.blockNumber) &&
      equalHash(block.hash,witness.blockHash),
      "canonical block changed during acceptance; PaymentUnknown, do not recharge");
  }
  // The ledger state may change while event receipts are inspected. Re-read
  // before emitting success and stop if the confirmed hashes drift.
  const finalTask=await (deps.readTask || readTask)(client,request.ledger,request.taskId);
  check(finalTask.status==="Proven" &&
    equalHash(finalTask.taskId,task.taskId) &&
    equalHash(finalTask.settlementRef,task.settlementRef) &&
    equalHash(finalTask.evidenceHash,task.evidenceHash) &&
    equalAddress(finalTask.payer,task.payer) &&
    finalTask.amountAtomic===task.amountAtomic,
    "ledger task changed during acceptance; PaymentUnknown, no recharge");
  return {
    schemaVersion:"agentledger.phase0.live-observation.v1",
    result:"PASS_OBSERVED_NOT_FINALITY",
    network:"MONAD_TESTNET_ONLY",
    chainId:10143,
    rpcPinnedToOfficialHTTPS:true,
    reviewedDeploymentRequired:true,
    deployment:reviewed,
    canonicalBlocksRecheckedAtEnd:true,
    orderedEventsVerified:true,
    taskReadbackRecheckedAtEnd:true,
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
    recorder:{...recorder, directCall:recorderCall},
    evidence:{
      transaction:anchor.transaction,
      blockHash:anchor.blockHash,
      evidenceHash:task.evidenceHash,
      bytesIndependentlyChecked:true,
      anchorCanonicalAtObservation:true,
      directCall:anchorCall,
    },
    executionAuthority:"ACP",
    walletKeysUsed:false,contractWritesPerformed:false,
    finalityProven:false,serviceDeliveryProven:false,
    sdkReady:false,
    caveat:"This is a point-in-time independently recheckable Testnet observation, not a signed proof of chain finality or Agent correctness.",
  };
}
