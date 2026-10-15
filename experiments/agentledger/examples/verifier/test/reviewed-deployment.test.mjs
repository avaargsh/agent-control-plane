// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { keccak256 } from "viem";
import {
  loadReviewedDeployments, validateReviewedRegistry, verifyReviewedDeployment,
} from "../lib/reviewed-deployment.mjs";

const LEDGER="0x"+"11".repeat(20);
const RECORDER="0x"+"22".repeat(20);
const CREATOR="0x"+"33".repeat(20);
const HASH="0x"+"44".repeat(32);
const BLOCK="0x"+"55".repeat(32);
const BYTECODE="0x6001600055";
const registry={
  schemaVersion:"agentledger.reviewed-deployments.v1",
  network:"eip155:10143",
  deployments:[{
    address:LEDGER,paymentRecorder:RECORDER,
    runtimeKeccak:keccak256(BYTECODE),
    deploymentTransaction:HASH,
    reviewReference:"https://github.com/avaargsh/agent-control-plane/pull/157"
  }]
};
function harness({runtime=BYTECODE,tx={},receipt={},block={}}={}) {
  return {
    getBytecode:async()=>runtime,
    getTransaction:async()=>({
      hash:HASH,from:CREATOR,to:null,input:"0x6001600055",...tx
    }),
    getTransactionReceipt:async()=>({
      status:"success",transactionHash:HASH,contractAddress:LEDGER,
      blockNumber:80n,blockHash:BLOCK,...receipt
    }),
    getBlock:async()=>({number:80n,hash:BLOCK,...block}),
  };
}
test("production registry is empty until independently reviewed deployment is merged",()=>{
  const loaded=loadReviewedDeployments();
  assert.equal(loaded.schemaVersion,"agentledger.reviewed-deployments.v1");
  assert.deepEqual(validateReviewedRegistry(loaded),[]);
  assert.throws(()=>validateReviewedRegistry({
    ...loaded,network:"eip155:1"
  }),/invalid Testnet/);
});
test("reviewed runtime code, direct creation tx, canonical receipt and authorized Recorder agree",async()=>{
  const verified=await verifyReviewedDeployment(harness(),LEDGER,RECORDER,registry);
  assert.equal(verified.address,LEDGER);
  assert.equal(verified.runtimeKeccak,keccak256(BYTECODE));
  assert.equal(verified.deploymentTransaction,HASH);
  assert.equal(verified.finalityProven,false);
});
test("unknown address cannot self-approve by submitting a valid-looking event",async()=>{
  await assert.rejects(
    ()=>verifyReviewedDeployment(harness(),CREATOR,RECORDER,registry),
    /no PR-reviewed/
  );
});
test("wrong runtime, Recorder, creation tx or block fails closed",async()=>{
  for(const [chain,recorder,pattern] of [
    [harness({runtime:"0x6000"}),RECORDER,/runtime code hash/],
    [harness(),CREATOR,/trusted Recorder/],
    [harness({tx:{to:CREATOR}}),RECORDER,/not a contract creation/],
    [harness({tx:{hash:"0x"+"aa".repeat(32)}}),RECORDER,/transaction missing or wrong/],
    [harness({receipt:{contractAddress:CREATOR}}),RECORDER,/does not create/],
    [harness({receipt:{status:"reverted"}}),RECORDER,/does not create/],
    [harness({block:{hash:"0x"+"bb".repeat(32)}}),RECORDER,/no longer canonical/],
  ]){
    await assert.rejects(()=>verifyReviewedDeployment(chain,LEDGER,recorder,registry),pattern);
  }
});
test("invalid or duplicate approval entries cannot silently expand registry",()=>{
  const clone=structuredClone(registry);
  clone.deployments.push({...clone.deployments[0],address:LEDGER.toUpperCase().replace("0X","0x")});
  assert.throws(()=>validateReviewedRegistry(clone),/duplicate/);
  for(const changes of [
    {reviewReference:"https://attacker.example/approve"},
    {runtimeKeccak:"0x1234"},
    {paymentRecorder:"0x1234"},
    {deploymentTransaction:"0x1234"},
  ]){
    const bad=structuredClone(registry);
    bad.deployments[0]={...bad.deployments[0],...changes};
    assert.throws(()=>validateReviewedRegistry(bad));
  }
});
