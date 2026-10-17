// SPDX-License-Identifier: Apache-2.0
// Hermetic UI fixtures. EVERY scenario is LOCAL SIMULATION, never real USDC.
// No external RPC, wallet or write operation is used to generate these facts.
import { evidenceHash, sha256Hex } from "./evidence-browser.mjs";
import { textStatistics } from "./text-stats.mjs";

const ID="0x"+"23".repeat(32);
const LEDGER="0x"+"42".repeat(20);
const CREATOR="0x"+"64".repeat(20);
const PAYER="0x"+"75".repeat(20);
const ZERO="0x"+"00".repeat(32);
const SETTLEMENT="0x"+"8a".repeat(32);
const INPUT=new TextEncoder().encode("Demo input only: summarize local evidence.\n");
export const SCENARIOS=Object.freeze([
  {
    key:"created",label:"01 · Created",short:"Task admitted",
    heading:"Task recorded",summary:"The ledger has a task, but there is no verified payment attempt. No execution decision is implied.",
  },
  {
    key:"paid",label:"02 · Paid claim",short:"Recorder attested",
    heading:"Recorder records payment",summary:"The contract marks Paid through its Recorder. This is not proof of a USDC Transfer or a consumed EIP-3009 nonce.",
  },
  {
    key:"proven",label:"03 · Evidence",short:"Digest anchored",
    heading:"Evidence ready to verify",summary:"The simulated contract has an Evidence hash. Click Verify to rehash local bytes; Recorder payment remains unverified.",
  },
  {
    key:"lost-ack",label:"04 · PaymentUnknown",short:"Lost ACK, hold charge",
    heading:"Payment response missing",summary:"A charge may have settled before the HTTP acknowledgement was lost. No receipt or consumed nonce is established; NEVER re-charge.",
  },
]);
export async function buildDemoCase(key) {
  if(!SCENARIOS.some(s=>s.key===key)) throw Error("Unknown local demonstration stage");
  const inputHash=await sha256Hex(INPUT);
  const toolResult={schemaVersion:"agentledger.demo-tool-result.v1",tool:"deterministic-text-stats",inputSha256:inputHash,...textStatistics(INPUT)};
  const outputBytes=new TextEncoder().encode(JSON.stringify(toolResult,null,2)+"\n");
  const evidence={
    completedAt:"2026-10-16T00:00:00.000Z",executor:"local-fixture",
    inputSha256:inputHash,modelOrTool:"deterministic-text-stats",
    outputSha256:await sha256Hex(outputBytes),schemaVersion:"agentledger.evidence.v1",taskId:ID,
  };
  const hash=await evidenceHash(evidence);
  const status=key==="created"?"Created":key==="proven"?"Proven":"Paid";
  const task={
    chainId:10143,contract:LEDGER,taskId:ID,
    status,creator:CREATOR,payer:status==="Created"?"0x"+"00".repeat(20):PAYER,
    amountAtomic:status==="Created"?"0":"1000",maxBudgetAtomic:"2000",
    descriptionHash:inputHash,evidenceHash:status==="Proven"?hash:ZERO,
    settlementRef:status==="Created"?ZERO:SETTLEMENT,
    createdAt:"1792110000",paidAt:status==="Created"?"0":"1792110010",
    provenAt:status==="Proven"?"1792110020":"0",
  };
  return {
    kind:"SIMULATION",key,stage:SCENARIOS.find(s=>s.key===key),
    task,evidence,inputBytes:INPUT,outputBytes,toolResult,
    payment:{
      status:"PaymentUnknown",
      recorderClaim:status==="Created"?"NotRecorded":"Paid",
      eventIndependentlyChecked:false,
      transferEvent:"NOT_OBSERVED_LOCAL_SIMULATION",
      authorizationUsed:"NOT_OBSERVED_LOCAL_SIMULATION",
      paymentNonce:null,
      transaction:null,
      settlementRef:status==="Created"?null:SETTLEMENT,
      recorderAttestationObserved:status!=="Created",
      failureSemantic:key==="lost-ack"?"ACK_LOST_OUTCOME_UNKNOWN":"RECORDER_CLAIM_NOT_USDC_PROOF",
      chargeAgain:false,
    },
    evidenceFact:status==="Proven"?"Simulated hash can be independently checked":"Not anchored",
  };
}
