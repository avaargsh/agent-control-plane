// SPDX-License-Identifier: Apache-2.0
// Black-box consumer EIP-3009 settlement observation with a hermetic,
// loopback JSON-RPC server. No upstream fixture parser or injected transport.
import test from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {createAgentLedgerVerifier} from "../dist/index.mjs";

const transaction="0x"+"c1".repeat(32),taskId="0x"+"01".repeat(32);
const blockHash="0x"+"b1".repeat(32),payer="0x"+"22".repeat(20);
const payTo="0x"+"33".repeat(20),nonce="0x"+"ad".repeat(32);
const token="0x"+"534b2f3A21130d7a6083"+"0c2Df862319e593943A3";
const transferTopic="0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef";
const authorizationUsed="0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5";
const topicAddress=a=>"0x"+"0".repeat(24)+a.slice(2);
const expected={
  network:"eip155:10143",taskId,transaction,payer,payTo,
  token,amountAtomic:"1000",paymentNonce:nonce,
};
async function localSettlement(t,{reorg=false,missingAuthorization=false}={}){
  const logs=[
    {address:token,topics:[transferTopic,topicAddress(payer),topicAddress(payTo)],
      data:"0x"+(1000n).toString(16).padStart(64,"0"),
      transactionHash:transaction,blockHash,logIndex:"0x0",removed:false},
    ...missingAuthorization?[]:[{
      address:token,topics:[authorizationUsed,topicAddress(payer),nonce],
      data:"0x",transactionHash:transaction,blockHash,logIndex:"0x1",removed:false,
    }]
  ];
  const receipt={transactionHash:transaction,blockHash,blockNumber:"0x64",
    transactionIndex:"0x1",status:"0x1",logs};
  const calls=[];
  const server=createServer(async(req,res)=>{
    try{
      if(req.method!=="POST"){res.writeHead(405).end();return;}
      let body="";for await(const part of req)body+=part;
      const {method,id}=JSON.parse(body);calls.push(method);
      const results={
        eth_chainId:"0x279f",
        eth_getTransactionReceipt:receipt,
        eth_blockNumber:"0x65",
        eth_getBlockByNumber:{number:"0x64",
          hash:reorg?"0x"+"ee".repeat(32):blockHash}
      };
      res.writeHead(200,{"content-type":"application/json"});
      res.end(JSON.stringify({jsonrpc:"2.0",id,result:results[method]??null}));
    }catch{res.writeHead(500).end();}
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  return {url:"http://127.0.0.1:"+server.address().port,calls};
}
test("valid Transfer and consumed nonce remain PaymentUnknown, never a signed charge approval",async t=>{
  const rpc=await localSettlement(t);
  const sdk=createAgentLedgerVerifier({rpcUrl:rpc.url});
  const output=await sdk.reconcile(expected);
  assert.equal(output.schemaVersion,"agentledger.sdk.read-only.v1");
  assert.equal(output.status,"PaymentUnknown");
  assert.equal(output.paymentEventObserved,true);
  assert.equal(output.nonce,nonce);
  assert.equal(output.transaction,transaction);
  assert.equal(output.chainId,10143);
  assert.equal(output.payerIntentVerified,false);
  assert.equal(output.prechargeReservationVerified,false);
  assert.equal(output.finalityProven,false);
  assert.equal(output.chargeAgain,false);
  assert.match(output.settlementRef,/^0x[0-9a-f]{64}$/);
  assert.deepEqual(rpc.calls,[
    "eth_chainId","eth_getTransactionReceipt","eth_blockNumber","eth_getBlockByNumber"
  ]);
});
test("missing AuthorizationUsed event gives deterministic settlement inconsistency, never unpaid",async t=>{
  const rpc=await localSettlement(t,{missingAuthorization:true});
  const sdk=createAgentLedgerVerifier({rpcUrl:rpc.url});
  await assert.rejects(()=>sdk.reconcile(expected),e=>{
    assert.equal(e.code,"SDK_SETTLEMENT_INCONSISTENT");
    assert.equal(e.retryable,false);
    assert.equal(e.chargeAgain,false);
    assert.equal(e.paymentStatus,"PaymentUnknown");
    return true;
  });
});
test("reorged canonical block fails closed and cannot produce receipt approval",async t=>{
  const rpc=await localSettlement(t,{reorg:true});
  const sdk=createAgentLedgerVerifier({rpcUrl:rpc.url});
  await assert.rejects(()=>sdk.reconcile(expected),e=>
    e.code==="SDK_SETTLEMENT_INCONSISTENT"&&e.operation==="reconcile"&&e.chargeAgain===false);
});
