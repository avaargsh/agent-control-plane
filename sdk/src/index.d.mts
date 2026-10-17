// SPDX-License-Identifier: Apache-2.0
/** @agentledger/verify: private read-only alpha.3 public type contract.
 * No signer, payment retry, fixture parser, injected transport or ACP API.
 */
export declare const SDK_VERSION: "0.2.0-alpha.3";
export declare const SDK_CAPABILITIES: Readonly<{
  observe:true;verify:true;reconcile:true;readOnly:true;
  acceptPayment:false;charge:false;sign:false;mutateLedger:false;
  executeAgent:false;manageAcp:false;proveChainFinality:false;
}>;
export type AgentLedgerSdkErrorCode =
  |"SDK_TRANSPORT_FORBIDDEN"|"SDK_INVALID_OPTIONS"|"SDK_INVALID_RPC_URL"
  |"SDK_INVALID_INPUT"|"SDK_INVALID_EVIDENCE"|"SDK_CHAIN_MISMATCH"
  |"SDK_EVIDENCE_MISMATCH"|"SDK_TASK_UNAVAILABLE"
  |"SDK_SETTLEMENT_INCONSISTENT"|"SDK_OBSERVATION_UNAVAILABLE";
export interface AgentLedgerSdkError extends Error {
  readonly name:"AgentLedgerSDKError";
  readonly code:AgentLedgerSdkErrorCode;
  readonly operation:"create"|"observe"|"verify"|"reconcile";
  /** Only the read-only operation may be retried. NEVER a payment. */
  readonly retryable:boolean;
  readonly paymentStatus:"PaymentUnknown";
  readonly chargeAgain:false;
}
export interface LedgerTask {
  readonly chainId:10143;
  readonly network:string;
  readonly contract:string;
  readonly taskId:string;
  readonly creator:string;readonly payer:string;
  readonly status:"Created"|"Paid"|"Proven";
  readonly statusValue:1|2|3;
  readonly descriptionHash:string;readonly settlementRef:string;
  readonly evidenceHash:string;
  readonly amountAtomic:string;readonly maxBudgetAtomic:string;
  readonly createdAt:string;readonly paidAt:string;readonly provenAt:string;
  readonly caveat:string;
}
export interface ReadonlyBoundary {
  readonly schemaVersion:"agentledger.sdk.read-only.v1";
  readonly sdkVersion:"0.2.0-alpha.3";
  readonly publicTestnetAcceptance:"BLOCKED_UNLESS_SEPARATELY_REVIEWED";
  readonly finalityProven:false;
  readonly chargeAgain:false;
  readonly executionAuthority:"ACP";
}
export interface ObserveResult extends ReadonlyBoundary {
  readonly source:"RPC_READ_ONLY";
  readonly task:LedgerTask;
  readonly paymentStatus:"PaymentUnknown";
  readonly paymentVerified:false;
  readonly status:{
    readonly schemaVersion:"agentledger.phase0.status.v1";
    readonly state:"PaymentUnknown";
    readonly ledgerState:"Created"|"Paid"|"Proven";
    readonly payment:Readonly<Record<string,unknown>>;
    readonly evidence:Readonly<Record<string,unknown>>;
    readonly retryPolicy:Readonly<{chargeAgain:false;action:string}>;
  };
}
export interface VerifyResult extends ReadonlyBoundary {
  readonly source:"CHAIN_ANCHOR_PLUS_LOCAL_BYTES";
  readonly match:true;readonly digest:string;
  readonly evidenceHash:string;
  readonly inputChecked:boolean;readonly outputChecked:boolean;
  readonly paymentStatus:"PaymentUnknown";
  readonly paymentVerified:false;
}
export interface ReceiptExpected {
  readonly network:"eip155:10143";
  readonly taskId:string;
  readonly transaction:string;
  readonly payer:string;
  readonly payTo:string;
  readonly token?:string; // official Monad Testnet USDC only
  readonly amountAtomic:string; // positive atomic-unit decimal string
  readonly paymentNonce:string; // consumed EIP-3009 nonce
}
export interface ReconcileResult extends ReadonlyBoundary {
  readonly source:"USDC_RECEIPT_ONLY";
  readonly paymentEventObserved:true;
  readonly nonce:string;readonly transaction:string;
  readonly blockHash:string;readonly settlementRef:string;
  readonly chainId:10143;
  /** Receipt observation cannot verify signed task intent / reservation. */
  readonly status:"PaymentUnknown";
  readonly payerIntentVerified:false;
  readonly prechargeReservationVerified:false;
  readonly note:string;
}
export interface AgentLedgerReadOnlyVerifier {
  readonly sdkVersion:"0.2.0-alpha.3";
  readonly capabilities:typeof SDK_CAPABILITIES;
  observe(contract:string,taskId:string):Promise<ObserveResult>;
  verify(contract:string,taskId:string,evidence:unknown,options?:{
    readonly inputBytes?:ArrayBuffer|ArrayBufferView|null;
    readonly outputBytes?:ArrayBuffer|ArrayBufferView|null;
  }):Promise<VerifyResult>;
  reconcile(expected:ReceiptExpected,options?:{
    readonly minConfirmations?:number;
  }):Promise<ReconcileResult>;
}
export declare function createAgentLedgerVerifier(options?:{
  readonly rpcUrl?:string;
}):AgentLedgerReadOnlyVerifier;
