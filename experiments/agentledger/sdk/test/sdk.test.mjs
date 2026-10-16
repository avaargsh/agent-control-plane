// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { encodeFunctionResult } from "viem";
import { TASK_ABI } from "../../examples/verifier/lib/task-reader.mjs";
import { evidenceHash } from "../../examples/verifier/public/evidence-browser.mjs";
import { SDK_VERSION, SDK_CAPABILITIES, createAgentLedgerVerifier } from "../dist/index.mjs";

const CONTRACT="0x"+"ab".repeat(20),ID="0x"+"11".repeat(32);
const payload={
  completedAt:"2026-10-16T00:00:00.000Z",
  executor:"demo-tool",inputSha256:"0x"+"33".repeat(32),
  modelOrTool:"bounded-statistics",outputSha256:"0x"+"44".repeat(32),
  schemaVersion:"agentledger.evidence.v1",taskId:ID,
};
async function localRpc(t,{chainId="0x279f",status=3}={}){
  const digest=await evidenceHash(payload);
  const task={
    creator:CONTRACT,payer:CONTRACT,amount:1000n,maxBudget:2000n,
    descriptionHash:"0x"+"22".repeat(32),evidenceHash:digest,
    settlementRef:"0x"+"55".repeat(32),status,
    createdAt:1n,paidAt:2n,provenAt:3n,
  };
  const encoded=encodeFunctionResult({abi:TASK_ABI,functionName:"getTask",result:task});
  const calls=[];
  const server=createServer(async(req,res)=>{
    let parsed;
    try{
      const chunks=[];for await(const chunk of req)chunks.push(chunk);
      parsed=JSON.parse(Buffer.concat(chunks).toString("utf8"));
      calls.push(parsed.method);
      const results={eth_chainId:chainId,eth_getCode:"0x6001",eth_call:encoded};
      const body=JSON.stringify({jsonrpc:"2.0",id:parsed.id,result:results[parsed.method]??null});
      res.writeHead(200,{"content-type":"application/json"});res.end(body);
    }catch(error){res.writeHead(500);res.end();}
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  return {url:"http://127.0.0.1:"+server.address().port,calls};
}
test("public package exports ONLY factory and metadata, no fixture parser or transport injector",async()=>{
  const exports=Object.keys(await import("../dist/index.mjs")).sort();
  assert.deepEqual(exports,["SDK_CAPABILITIES","SDK_VERSION","createAgentLedgerVerifier"]);
  assert.equal(SDK_VERSION,"0.2.0-alpha.2");
  for(const api of ["observe","verify","reconcile","readOnly"]) assert.equal(SDK_CAPABILITIES[api],true);
  for(const api of ["charge","sign","mutateLedger","manageAcp","executeAgent"])assert.equal(SDK_CAPABILITIES[api],false);
  const sdk=createAgentLedgerVerifier({rpcUrl:"http://127.0.0.1:18545"});
  assert.deepEqual(Object.keys(sdk).sort(),["capabilities","observe","reconcile","sdkVersion","verify"]);
  assert.equal(Object.isFrozen(sdk),true);
});
test("injected client/transport rejected at construction BEFORE any nonce/RPC",()=>{
  for(const key of ["client","transport","fetch","request"]){
    assert.throws(()=>createAgentLedgerVerifier({[key]:{},rpcUrl:"http://127.0.0.1:18545"}),
      /Injected transport\/client is forbidden/);
  }
  assert.throws(()=>createAgentLedgerVerifier({unexpected:true}),/Unknown SDK option/);
});
test("real JSON-RPC consumer observes recorder claim but PUBLIC state remains PaymentUnknown",async t=>{
  const rpc=await localRpc(t);
  const sdk=createAgentLedgerVerifier({rpcUrl:rpc.url});
  const observed=await sdk.observe(CONTRACT,ID);
  assert.equal(observed.task.status,"Proven");
  assert.equal(observed.task.descriptionHash,"0x"+"22".repeat(32));
  assert.equal(observed.status.state,"PaymentUnknown");
  assert.equal(observed.chargeAgain,false);
  assert.equal(observed.publicTestnetAcceptance,"BLOCKED_UNLESS_SEPARATELY_REVIEWED");
  assert.ok(rpc.calls.includes("eth_chainId"));
  assert.ok(rpc.calls.includes("eth_getCode"));
});
test("verify matches local Evidence bytes but never upgrades payment",async t=>{
  const rpc=await localRpc(t);
  const sdk=createAgentLedgerVerifier({rpcUrl:rpc.url});
  const result=await sdk.verify(CONTRACT,ID,payload);
  assert.equal(result.match,true);
  assert.equal(result.paymentVerified,false);
  assert.equal(result.paymentStatus,"PaymentUnknown");
  await assert.rejects(()=>sdk.verify(CONTRACT,ID,{...payload,executor:"tampered"}),/SHA-256 mismatch/);
});
test("wrong chain fails and missing nonce cannot trigger read-only receipt request",async t=>{
  const rpc=await localRpc(t,{chainId:"0x1"});
  const sdk=createAgentLedgerVerifier({rpcUrl:rpc.url});
  await assert.rejects(()=>sdk.observe(CONTRACT,ID),/wrong chain/);
  const before=rpc.calls.length;
  await assert.rejects(()=>sdk.reconcile({transaction:"0x"+"77".repeat(32)}),
    /paymentNonce required/);
  assert.equal(rpc.calls.length,before);
});
