// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {observeTaskReadOnly,resolveVerifierUrl,taskReadSummary,validateTaskIdentity} from "./observe";
const C="0x"+"11".repeat(20),TASK="0x"+"22".repeat(32);
const good={
 chainId:10143,contract:C,taskId:TASK,creator:C,payer:C,
 status:"Proven",amountAtomic:"1000",maxBudgetAtomic:"2000",
 descriptionHash:"0x"+"33".repeat(32),
 evidenceHash:"0x"+"44".repeat(32),settlementRef:"0x"+"55".repeat(32),
};
test("lookup only permits explicit valid chain and exact requested identity",()=>{
 const task=validateTaskIdentity(good,C,TASK);
 assert.equal(task.status,"Proven");
 for(const bad of [
  {...good,chainId:1},{...good,contract:"0x"+"99".repeat(20)},
  {...good,taskId:"0x"+"ff".repeat(32)}
 ])assert.throws(()=>validateTaskIdentity(bad,C,TASK),/CHAIN_TASK_IDENTITY_MISMATCH/);
 assert.throws(()=>validateTaskIdentity({...good,descriptionHash:"0x0"},C,TASK),/INVALID_CHAIN_HASH/);
 assert.throws(()=>validateTaskIdentity({...good,amountAtomic:"-1"},C,TASK),/INVALID_CHAIN_AMOUNT/);
 assert.throws(()=>validateTaskIdentity({...good,status:"Done"},C,TASK),/INVALID_CHAIN_TASK_STATUS/);
});
test("verifier URL restriction refuses private HTTP and URL/userinfo injection",()=>{
 assert.equal(resolveVerifierUrl("http://127.0.0.1:4321"),"http://127.0.0.1:4321");
 assert.equal(resolveVerifierUrl("https://example.invalid"),"https://example.invalid");
 for(const uri of [
   "http://10.1.2.3:4321","http://example.com",
   "https://x.invalid/path","https://x.invalid/?method=eth_sendTransaction",
   "https://user:pass@x.invalid/"
 ])assert.throws(()=>resolveVerifierUrl(uri),/VERIFIER/);
});
test("real GET-only HTTP adapter observes task while payment remains unknown",async t=>{
 let count=0;
 const server=createServer((req,res)=>{
  count++;
  assert.equal(req.method,"GET");
  assert.ok(req.url?.startsWith("/api/task?contract="));
  res.writeHead(200,{"content-type":"application/json"});
  res.end(JSON.stringify(good));
 });
 await new Promise<void>(resolve=>server.listen(0,"127.0.0.1",resolve));
 t.after(()=>new Promise<void>(resolve=>server.close(()=>resolve())));
 const addr=server.address();
 if(!addr||typeof addr==="string")throw Error("unexpected TCP binding");
 const observed=await observeTaskReadOnly(C,TASK,"http://127.0.0.1:"+addr.port);
 assert.equal(count,1);
 assert.equal(observed.source,"TESTNET_TASK_ONLY");
 assert.equal(observed.paymentStatus,"PaymentUnknown");
 assert.equal(observed.chargeAgain,false);
 assert.equal(observed.publicTestnetAcceptance,"BLOCKED_NO_REVIEWED_DEPLOYMENT");
 const summary=taskReadSummary(observed.task);
 assert.equal(summary.mode,"TESTNET_TASK_READ_ONLY");
 assert.equal(summary.payment.observed,"PaymentUnknown");
 assert.equal(summary.security.publicTestnetAcceptance,"BLOCKED_NO_REVIEWED_DEPLOYMENT");
});
test("bad address aborts before HTTP and cannot smuggle another origin",async()=>{
 await assert.rejects(()=>observeTaskReadOnly(C,"evil","http://127.0.0.1:4321"),/INVALID_TASK_LOOKUP/);
 await assert.rejects(()=>observeTaskReadOnly(C,TASK,"http://10.10.10.10:4000"),/VERIFIER/);
});

test("observer rejects HTML, oversized JSON and HTTP redirects before validating task",async t=>{
 let kind="html";
 const server=createServer((req,res)=>{
  if(kind==="html"){
   res.writeHead(200,{"content-type":"text/html"});
   res.end("<script>fake task</script>");
  }else if(kind==="large"){
   res.writeHead(200,{"content-type":"application/json","content-length":"70000"});
   res.end(" ".repeat(70000));
  }else if(kind==="unannounced-large"){
   res.writeHead(200,{"content-type":"application/json"});
   res.end(JSON.stringify({...good,padding:"x".repeat(70000)}));
  }else{
   res.writeHead(302,{"location":"https://example.invalid/api/task"});
   res.end();
  }
 });
 await new Promise<void>(resolve=>server.listen(0,"127.0.0.1",resolve));
 t.after(()=>new Promise<void>(resolve=>server.close(()=>resolve())));
 const addr=server.address();
 if(!addr||typeof addr==="string")throw Error("invalid test server");
 const base="http://127.0.0.1:"+addr.port;
 await assert.rejects(()=>observeTaskReadOnly(C,TASK,base),/TASK_READ_CONTENT_TYPE_INVALID/);
 kind="large";
 await assert.rejects(()=>observeTaskReadOnly(C,TASK,base),/TASK_READ_RESPONSE_TOO_LARGE/);
 kind="unannounced-large";
 await assert.rejects(()=>observeTaskReadOnly(C,TASK,base),/TASK_READ_RESPONSE_TOO_LARGE/);
 kind="redirect";
 await assert.rejects(()=>observeTaskReadOnly(C,TASK,base));
});
