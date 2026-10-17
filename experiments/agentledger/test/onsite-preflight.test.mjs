// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {mkdtempSync,existsSync,readFileSync,rmSync,writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {localPreflight,networkPreflight,runPreflight,MONAD_TESTNET} from "../tools/onsite-preflight.mjs";

test("official Testnet identity and offline default expose no payment claim",async()=>{
 let fetchCalls=0;
 const result=await runPreflight({fetchImpl:async()=>{fetchCalls++;throw Error("SHOULD_BE_OFFLINE");}});
 assert.equal(fetchCalls,0);
 assert.equal(result.mode,"LOCAL_ONLY_NO_NETWORK");
 assert.equal(result.testnet.chainId,10143);
 assert.equal(result.testnet.network,"eip155:10143");
 assert.equal(result.testnet.usdc,"0x534b2f3A21130d7a60830c2Df862319e593943A3");
 assert.equal(result.files.ready,true);
 assert.equal(result.safeToDemo,true);
 assert.equal(result.deployment.registryState,"NO_REVIEWED_DEPLOYMENTS");
 assert.equal(result.deployment.publicPaidAcceptance,"BLOCKED_UNLESS_INDEPENDENTLY_REVIEWED");
 assert.equal(result.safeToClaimPublicTestnetAcceptance,false);
 assert.equal(result.payment.state,"PaymentUnknown");
 assert.equal(result.payment.chargeAgain,false);
 assert.equal(result.payment.actualUSDCSpend,false);
 assert.equal(result.testnet.networkContacted,false);
});
test("local missing component fails safe to demo while payment acceptance always blocked",()=>{
 const missing=localPreflight({hasFile:()=>false});
 assert.equal(missing.files.ready,false);
 assert.equal(missing.safeToDemo,false);
 assert.ok(missing.files.missing.includes("apps/pocket/App.tsx"));
 assert.equal(missing.safeToClaimPublicTestnetAcceptance,false);
});
test("read-only network probe checks eth_chainId and advertised x402 exact, not settlement",async()=>{
 let calls=[];
 const fetchImpl=async (url,options)=>{
  calls.push({url:String(url),method:options.method});
  assert.notEqual(options.method,"eth_sendTransaction");
  if(String(url).includes("testnet-rpc.monad.xyz"))
   return {ok:true,json:async()=>({jsonrpc:"2.0",id:1,result:"0x279f"})};
  if(String(url).endsWith("/supported"))
   return {ok:true,json:async()=>({kinds:[{network:"eip155:10143",scheme:"exact"}]})};
  throw Error("UNEXPECTED_NETWORK");
 };
 const report=await runPreflight({network:true,fetchImpl});
 assert.deepEqual(calls.map(x=>x.method),["POST","GET"]);
 assert.equal(report.network.rpc.status,"MATCHED_TESTNET_10143");
 assert.equal(report.network.facilitator.advertisedMonadExact,true);
 assert.equal(report.payment.independentlyVerified,false);
 assert.equal(report.safeToClaimPublicTestnetAcceptance,false);
 assert.equal(report.deployment.publicPaidAcceptance,"BLOCKED_UNLESS_INDEPENDENTLY_REVIEWED");
});
test("wrong chain and unadvertised x402 stay explicit failures",async()=>{
 const network=await networkPreflight({fetchImpl:async (url)=>{
  if(String(url).includes("testnet-rpc.monad.xyz"))
   return {ok:true,json:async()=>({jsonrpc:"2.0",id:1,result:"0x8f"})};
  return {ok:true,json:async()=>({kinds:[{network:"eip155:1",scheme:"exact"}]})};
 }});
 assert.equal(network.rpc.status,"WRONG_CHAIN_ID");
 assert.equal(network.facilitator.status,"NOT_ADVERTISED");
 assert.equal(network.facilitator.advertisedMonadExact,false);
});
test("CLI can safely write unique evidence, never overwrite old report",()=>{
 const dir=mkdtempSync(join(tmpdir(),"agentledger-onsite-"));
 const cli=fileURLToPath(new URL("../tools/onsite-preflight.mjs",import.meta.url));
 const output=join(dir,"readiness.json");
 try{
  const first=spawnSync(process.execPath,[cli,"--output",output],{encoding:"utf8"});
  assert.equal(first.status,0,first.stderr);
  assert.equal(existsSync(output),true);
  const report=JSON.parse(readFileSync(output,"utf8"));
  assert.equal(report.mode,"LOCAL_ONLY_NO_NETWORK");
  assert.equal(report.safeToClaimPublicTestnetAcceptance,false);
  const second=spawnSync(process.execPath,[cli,"--output",output],{encoding:"utf8"});
  assert.equal(second.status,2);
  assert.equal(JSON.parse(readFileSync(output,"utf8")).payment.state,"PaymentUnknown");
  const noArgs=spawnSync(process.execPath,[cli],{encoding:"utf8"});
  assert.equal(noArgs.status,0,noArgs.stderr);
  assert.equal(JSON.parse(noArgs.stdout).safeToDemo,true);
 }finally{rmSync(dir,{recursive:true,force:true});}
});
