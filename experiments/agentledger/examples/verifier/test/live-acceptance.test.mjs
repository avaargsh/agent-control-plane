// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import {
  encodeAbiParameters, encodeEventTopics, parseAbiItem,
} from "viem";
import {
  assertPublicTestnetRpc, runLiveAcceptance, validateAcceptanceRequest,
} from "../lib/live-acceptance.mjs";

const LEDGER="0x"+"11".repeat(20);
const PAYER="0x"+"22".repeat(20);
const CREATOR="0x"+"33".repeat(20);
const RECORDER="0x"+"44".repeat(20);
const TASK="0x"+"55".repeat(32);
const REF="0x"+"66".repeat(32);
const EVIDENCE_HASH="0x"+"77".repeat(32);
const PAYMENT_TX="0x"+"88".repeat(32);
const RECORDER_TX="0x"+"99".repeat(32);
const ANCHOR_TX="0x"+"aa".repeat(32);
const PAYMENT_BLOCK="0x"+"bb".repeat(32);
const RECORDER_BLOCK="0x"+"cc".repeat(32);
const ANCHOR_BLOCK="0x"+"dd".repeat(32);
const paymentABI=parseAbiItem(
  "event PaymentAttested(bytes32 indexed taskId, address indexed payer, uint256 amount, bytes32 indexed settlementRef)"
);
const anchorABI=parseAbiItem(
  "event EvidenceAnchored(bytes32 indexed taskId, bytes32 indexed evidenceHash, address indexed submitter)"
);

const request={
  schemaVersion:"agentledger.phase0.live-acceptance.v1",
  network:"eip155:10143",
  ledger:LEDGER,taskId:TASK,attempt:"Reserved",
  payment:{approval:{signed:true},pendingReceipt:{transaction:PAYMENT_TX},expectedPayTo:PAYER},
  evidence:{file:"evidence.json",inputFile:"input.txt",outputFile:"output.json"},
  recorderTransaction:RECORDER_TX,anchorTransaction:ANCHOR_TX,
};
function receipt(tx,block,number,log) {
  return {
    status:"success",transactionHash:tx,blockNumber:number,blockHash:block,
    logs:[{address:LEDGER,transactionHash:tx,blockHash:block,removed:false,...log}],
  };
}
function recorderReceipt({settlementRef=REF,amount=1000n,payer=PAYER}={}) {
  return receipt(RECORDER_TX,RECORDER_BLOCK,100n,{
    topics:encodeEventTopics({abi:[paymentABI],eventName:"PaymentAttested",
      args:{taskId:TASK,payer,settlementRef}}),
    data:encodeAbiParameters([{type:"uint256"}],[amount]),
  });
}
function anchorReceipt({hash=EVIDENCE_HASH,submitter=CREATOR}={}) {
  return receipt(ANCHOR_TX,ANCHOR_BLOCK,101n,{
    topics:encodeEventTopics({abi:[anchorABI],eventName:"EvidenceAnchored",
      args:{taskId:TASK,evidenceHash:hash,submitter}}),
    data:"0x",
  });
}
const task={
  chainId:10143,status:"Proven",taskId:TASK,contract:LEDGER,
  creator:CREATOR,payer:PAYER,amountAtomic:"1000",
  settlementRef:REF,evidenceHash:EVIDENCE_HASH,
};
function harness({phase0={},payEvent={},proofEvent={},reorg=false}={}) {
  let inspected=0,chainReads=0;
  const phase={
    schemaVersion:"agentledger.phase0.status.v1",state:"EvidenceAnchored",
    payment:{eventIndependentlyChecked:true,transaction:PAYMENT_TX,
      authorizationNonce:"0x"+"ff".repeat(32),settlementRef:REF},
    evidence:{bytesIndependentlyChecked:true,onchainHash:EVIDENCE_HASH},
    retryPolicy:{chargeAgain:false},
    inspection:{paymentWitness:{
      transaction:PAYMENT_TX,blockHash:PAYMENT_BLOCK,blockNumber:"99",
      canonicalBlockCheckedAtObservation:true,
    }},
    ...phase0
  };
  const receipts={
    [RECORDER_TX]:recorderReceipt(payEvent),
    [ANCHOR_TX]:anchorReceipt(proofEvent),
  };
  const client={
    getChainId:async()=>10143,
    getTransactionReceipt:async({hash})=>{chainReads++;return receipts[hash];},
    getBlock:async({blockNumber})=>({
      number:blockNumber,
      hash:reorg&&blockNumber===101n ? RECORDER_BLOCK :
        blockNumber===101n?ANCHOR_BLOCK:RECORDER_BLOCK
    }),
    readContract:async()=>RECORDER,
  };
  return {
    deps:{rpc:"https://testnet-rpc.monad.xyz",client,
      inspect:async()=>{inspected++;return phase;},
      readTask:async()=>task,
    },
    count:()=>({inspected,chainReads}),
  };
}
test("ONLY pinned HTTPS official Testnet endpoint is eligible for a live claim",()=>{
  assert.equal(assertPublicTestnetRpc("https://testnet-rpc.monad.xyz"),
    "https://testnet-rpc.monad.xyz/");
  for(const url of [
    "http://127.0.0.1:18545","http://testnet-rpc.monad.xyz",
    "https://testnet-rpc.monad.xyz.evil.example",
    "https://testnet-rpc.monad.xyz/?test=1",
    "https://testnet-rpc.monad.xyz/extra",
  ]) assert.throws(()=>assertPublicTestnetRpc(url),/pinned official/);
  assert.throws(()=>validateAcceptanceRequest({...request,attempt:"NotStarted"}),/reserved/);
  assert.throws(()=>validateAcceptanceRequest({...request,evidence:{file:"evidence.json"}}),/original input/);
});
test("accepted observation includes independent Recorder and Evidence events but NEVER finality",async()=>{
  const h=harness();
  const out=await runLiveAcceptance(request,h.deps);
  assert.equal(out.result,"PASS_OBSERVED_NOT_FINALITY");
  assert.equal(out.phase0State,"EvidenceAnchored");
  assert.equal(out.recorder.event,"PaymentAttested");
  assert.equal(out.evidence.evidenceHash,EVIDENCE_HASH);
  assert.equal(out.walletKeysUsed,false);
  assert.equal(out.finalityProven,false);
  assert.equal(out.serviceDeliveryProven,false);
  assert.equal(out.sdkReady,false);
  assert.deepEqual(h.count(),{inspected:1,chainReads:2});
});
test("PaymentUnknown or missing canonical payment witness fails before Recorder RPC",async()=>{
  for(const phase0 of [
    {state:"PaymentUnknown"},
    {inspection:{paymentWitness:{canonicalBlockCheckedAtObservation:false}}},
    {payment:{eventIndependentlyChecked:false}},
    {evidence:{bytesIndependentlyChecked:false}},
  ]){
    const h=harness({phase0});
    await assert.rejects(()=>runLiveAcceptance(request,h.deps));
    assert.equal(h.count().chainReads,0);
  }
});
test("Recorder event amount/recipient/settlement reference substitution is rejected",async()=>{
  for(const payEvent of [
    {amount:999n},{payer:CREATOR},{settlementRef:EVIDENCE_HASH},
  ]){
    await assert.rejects(()=>runLiveAcceptance(request,harness({payEvent}).deps));
  }
});
test("wrong Evidence anchored hash, unauthorized submitter, or reorged block fails",async()=>{
  for(const proofEvent of [
    {hash:REF},{submitter:PAYER},
  ]){
    await assert.rejects(()=>runLiveAcceptance(request,harness({proofEvent}).deps));
  }
  await assert.rejects(()=>runLiveAcceptance(request,harness({reorg:true}).deps),/no longer canonical/);
});
test("fake local Anvil, even with chain ID 10143, cannot masquerade as public Testnet",async()=>{
  const h=harness();
  await assert.rejects(()=>runLiveAcceptance(request,{
    ...h.deps,rpc:"http://127.0.0.1:18545",
  }),/pinned official/);
  assert.deepEqual(h.count(),{inspected:0,chainReads:0});
});
