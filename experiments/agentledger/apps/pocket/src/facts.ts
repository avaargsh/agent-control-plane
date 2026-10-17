// All bundled scenarios are demonstration fixtures: no live RPC or USDC payment.
import { DEMO_EXPECTED_HASH } from "./evidence";
export type Stage = "created" | "paid" | "evidence" | "unknown";
export type DemoFact = {
  stage: Stage;
  state: "Created" | "Paid" | "Proven";
  payment: "PaymentUnknown";
  recorderClaim: "NotRecorded" | "Paid";
  taskId: string;
  descriptionHash: string;
  evidenceHash: string;
  ledger: string;
  chainId: 10143;
  rpc: string;
  transferEvent: "NOT_VERIFIED";
  authorizationUsed: "NOT_VERIFIED";
  paymentNonce: null;
  chargeAgain: false;
  reviewedDeployment: false;
  source: "LOCAL_SIMULATION";
};
export const RPC_ID = "https://testnet-rpc.monad.xyz/ (identity only)";
const prefix=(hex:string,size:number)=>"0x"+hex.repeat(size);
const zero=prefix("00",32);
const base = {
 taskId:prefix("23",32),descriptionHash:prefix("a1",32),
 ledger:prefix("42",20),chainId:10143 as const,rpc:RPC_ID,
 transferEvent:"NOT_VERIFIED" as const,
 authorizationUsed:"NOT_VERIFIED" as const,paymentNonce:null,chargeAgain:false as const,
 reviewedDeployment:false as const,source:"LOCAL_SIMULATION" as const,
 payment:"PaymentUnknown" as const,
};
export const FACTS:Record<Stage,DemoFact>={
 created:{...base,stage:"created",state:"Created",recorderClaim:"NotRecorded",evidenceHash:zero},
 paid:{...base,stage:"paid",state:"Paid",recorderClaim:"Paid",evidenceHash:zero},
 evidence:{...base,stage:"evidence",state:"Proven",recorderClaim:"Paid",evidenceHash:DEMO_EXPECTED_HASH},
 unknown:{...base,stage:"unknown",state:"Paid",recorderClaim:"Paid",evidenceHash:zero},
};
export function shorten(hex:string){return hex.length>22?hex.slice(0,11)+"…"+hex.slice(-8):hex;}
export function exportSummary(fact:DemoFact,bytesChecked=false){
 return {
   schemaVersion:"agentledger.pocket.summary.v1",mode:"LOCAL_SIMULATION",
   outcome:"SIMULATED_ONLY_NOT_TESTNET_ACCEPTANCE",
   task:{taskId:fact.taskId,descriptionHash:fact.descriptionHash,ledgerState:fact.state},
   chainId:fact.chainId,contract:fact.ledger,
   rpc:{identity:fact.rpc,contacted:false},
   payment:{observed:"PaymentUnknown",recorderClaim:fact.recorderClaim,transferEvent:fact.transferEvent,
      authorizationUsed:fact.authorizationUsed,nonce:null,chargeAgain:false},
   evidence:{evidenceHash:fact.evidenceHash,verification:bytesChecked?"SIMULATED_HASH_MATCHED_TO_PINNED_REFERENCE":"NOT_VERIFIED"},
   security:{reviewedDeployment:false,publicTestnetAcceptance:"BLOCKED_NO_REVIEWED_DEPLOYMENT",
      mainnetAccepted:false,realFundsUsed:false,executionAuthority:"UNVERIFIED_EXTERNAL"},
 };
}
