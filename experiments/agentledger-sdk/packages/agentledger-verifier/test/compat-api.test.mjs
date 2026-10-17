// SPDX-License-Identifier: Apache-2.0
// Compatibility contract: the duplicate *UI* is retired, NOT the API.
import test from "node:test";
import assert from "node:assert/strict";
import { createVerifierServer } from "../dist/server.js";
const C="0x"+"22".repeat(20),T="0x"+"11".repeat(32);
test("retired SDK verifier UI refuses old homepage while GET-only API remains available",async()=>{
 const server=createVerifierServer({lookup:async(contract,id)=>({
  chainId:10143,contract,taskId:id,status:"Created"
 })});
 await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
 const url="http://127.0.0.1:"+server.address().port;
 try{
  const home=await fetch(url+"/");
  assert.equal(home.status,410);
  const retired=await home.json();
  assert.equal(retired.code,"LEGACY_VERIFIER_UI_RETIRED");
  assert.equal(retired.paymentStatus,"PaymentUnknown");
  const health=await fetch(url+"/api/healthz");
  assert.equal(health.status,200);
  assert.equal((await health.json()).mode,"read-only");
  const read=await fetch(url+"/api/task?"+new URLSearchParams({contract:C,taskId:T}));
  assert.equal(read.status,200);
  const obj=await read.json();assert.equal(obj.taskId,T);
  const post=await fetch(url+"/api/task",{method:"POST"});
  assert.equal(post.status,405);
  const unknown=await fetch(url+"/app.mjs");
  assert.equal(unknown.status,404);
 }finally{await new Promise(resolve=>server.close(resolve));}
});
