// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { createVerifierServer } from "../server.mjs";
import { buildDemoCase } from "../public/demo-scenarios.mjs";
import { verifyEvidence } from "../public/evidence-browser.mjs";

test("local visual demo is source-labeled and cannot report independently paid USDC",async()=>{
  for(const key of ["created","lost-ack","proven"]){
    const view=await buildDemoCase(key);
    assert.equal(view.kind,"SIMULATION");
    assert.equal(view.payment.status,"PaymentUnknown");
    assert.equal(view.payment.eventIndependentlyChecked,false);
    assert.equal(view.payment.chargeAgain,false);
    assert.equal(view.task.chainId,10143);
    assert.equal(view.task.contract.length,42);
    assert.equal(view.task.taskId.length,66);
  }
  await assert.rejects(()=>buildDemoCase("live"),/Unknown/);
});
test("Proven fixture verifies bytes without acquiring a payment-success claim",async()=>{
  const scenario=await buildDemoCase("proven");
  const match=await verifyEvidence(scenario.evidence,scenario.task,
    scenario.inputBytes,scenario.outputBytes);
  assert.equal(match.match,true);
  assert.equal(match.inputChecked,true);
  assert.equal(match.outputChecked,true);
  assert.equal(scenario.payment.status,"PaymentUnknown");
  await assert.rejects(
    ()=>verifyEvidence(scenario.evidence,scenario.task,
      new TextEncoder().encode("tampered"),scenario.outputBytes),
    /Input bytes/
  );
});
test("new studio and demo modules are only served locally with read-only CSP",async()=>{
  const server=createVerifierServer({
    lookup:async()=>{throw Error("simulated UI must not query RPC");}
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  const base="http://127.0.0.1:"+server.address().port;
  try{
    const root=await fetch(base+"/");
    assert.equal(root.status,200);
    const page=await root.text();
    for(const phrase of [
      "Verification workspace","LOCAL SIMULATION","PaymentUnknown",
      "Evidence lab","SDK v0.2 alpha","mode-demo","mode-live",
    ])assert.ok(page.includes(phrase),"missing Studio element: "+phrase);
    const js=await fetch(base+"/demo-scenarios.mjs");
    assert.equal(js.status,200);
    assert.match(js.headers.get("content-type"),/javascript/);
    assert.match(js.headers.get("content-security-policy"),/script-src 'self'/);
    const body=await js.text();
    assert.match(body,/LOCAL SIMULATION/);
    for(const endpoint of ["/api/pay","/api/charge","/api/execute"]){
      const response=await fetch(base+endpoint,{method:"POST"});
      assert.equal(response.status,405);
    }
  }finally{
    await new Promise(resolve=>server.close(resolve));
  }
});
