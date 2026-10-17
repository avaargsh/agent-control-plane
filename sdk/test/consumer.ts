// SPDX-License-Identifier: Apache-2.0
// Type-only black-box consumer: no private source path is imported.
import {createAgentLedgerVerifier,SDK_VERSION} from "../dist/index.mjs";
import type {AgentLedgerSdkError} from "../dist/index.mjs";
const sdk=createAgentLedgerVerifier({rpcUrl:"https://testnet-rpc.monad.xyz"});
const checkVersion:"0.2.0-alpha.3"=SDK_VERSION;
void checkVersion;
async function exercise(contract:string,taskId:string){
  const observation=await sdk.observe(contract,taskId);
  const unknown:"PaymentUnknown"=observation.status.state;
  const fact=await sdk.verify(contract,taskId,{},{
    inputBytes:new Uint8Array([1,2]),outputBytes:new ArrayBuffer(8)
  });
  const remainsUnknown:"PaymentUnknown"=fact.paymentStatus;
  const receipt=await sdk.reconcile({
    network:"eip155:10143",taskId,transaction:taskId,payer:contract,
    payTo:contract,amountAtomic:"1000",paymentNonce:taskId,
  },{minConfirmations:2});
  const unbound:"PaymentUnknown"=receipt.status;
  void [unknown,remainsUnknown,unbound];
}
void exercise;
function classify(e:unknown) {
  if(e instanceof Error&&"code" in e){
    const code=(e as AgentLedgerSdkError).code;
    const noCharge:false=(e as AgentLedgerSdkError).chargeAgain;
    void [code,noCharge];
  }
}
void classify;
// @ts-expect-error no injected transport allowed in SDK construction
createAgentLedgerVerifier({transport:{}});
// @ts-expect-error no private wallet/chain-write method
sdk.recordPayment("0x","0x");
// @ts-expect-error no public fixture parser
sdk.verifyReceipt({});
// @ts-expect-error receipt paymentNonce is mandatory
sdk.reconcile({network:"eip155:10143",taskId:"0x",transaction:"0x",payer:"0x",payTo:"0x",amountAtomic:"1000"});
