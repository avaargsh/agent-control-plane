// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { createVerifierServer } from "../server.mjs";
import { runDemoJourney } from "../tools/demo-journey.mjs";
import { makeSummary } from "../public/summary.mjs";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildDemoCase } from "../public/demo-scenarios.mjs";
import { verifyEvidence } from "../public/evidence-browser.mjs";

test("local visual demo is source-labeled and cannot report independently paid USDC",async()=>{
  for(const key of ["created","paid","proven","lost-ack"]){
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
      "task-description","task-contract","task-rpc","save-summary",
      "payment-transfer","payment-nonce","data-scenario=\"paid\"",
    ])assert.ok(page.includes(phrase),"missing Studio element: "+phrase);
    const js=await fetch(base+"/demo-scenarios.mjs");
    const summaryModule=await fetch(base+"/summary.mjs");
    assert.equal(summaryModule.status,200);
    assert.match(await summaryModule.text(),/BLOCKED_NO_REVIEWED_DEPLOYMENT/);
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

test("CLI runs Created→Paid→Evidence→Verify, unknown ACK and rejects cross-Ledger/payer",async()=>{
  const report=await runDemoJourney();
  const steps=report.journey.steps.map(step=>step.step);
  assert.deepEqual(steps,["created","paid","proven","verify","lost-ack","cross-ledger","wrong-payer"]);
  assert.equal(report.journey.createdToRecorderPaidToEvidenceToVerify,"PASS_SIMULATED_ONLY");
  assert.equal(report.journey.paymentUnknownLostAck,"PASS_NO_RECHARGE");
  assert.equal(report.journey.crossLedgerRejected,true);
  assert.equal(report.journey.wrongIdentityRejected,true);
  assert.equal(report.mode,"LOCAL_SIMULATION");
  assert.equal(report.payment.observedState,"PaymentUnknown");
  assert.equal(report.evidence.verification,"HASH_MATCHED");
  assert.equal(report.evidence.bytesIndependentlyChecked,true);
  assert.equal(report.rpc.contacted,false);
  assert.equal(report.boundaries.publicTestnetAcceptance,"BLOCKED_NO_REVIEWED_DEPLOYMENT");
  assert.equal(report.boundaries.isTestnetPaymentAcceptance,false);
  assert.equal(report.boundaries.isMainnetAcceptance,false);
  assert.equal(report.payment.paymentNonce,null);
  assert.match(report.task.descriptionHash,/^0x[0-9a-f]{64}$/);
  assert.match(report.contract,/^0x[0-9a-f]{40}$/);
  assert.match(report.evidence.evidenceHash,/^0x[0-9a-f]{64}$/);
  const original=await buildDemoCase("paid");
  assert.throws(()=>makeSummary({...original.task,contract:"0x1234"}),
    /identity/);
});
test("public Testnet CLI fails before RPC when reviewed deployment registry is empty",()=>{
  const tmp=mkdtempSync(join(tmpdir(),"agentledger-studio-"));
  try{
    const input=join(tmp,"request.json");
    const output=join(tmp,"live-summary.json");
    writeFileSync(input,JSON.stringify({
      schemaVersion:"agentledger.phase0.live-acceptance.v1",
      network:"eip155:10143",ledger:"0x"+"42".repeat(20),
      taskId:"0x"+"23".repeat(32),
    }));
    const result=spawnSync(process.execPath,[
      new URL("../tools/live-acceptance.mjs",import.meta.url).pathname,input,output
    ],{encoding:"utf8",timeout:8000,env:{...process.env,MONAD_RPC_URL:"http://127.0.0.1:9"}});
    assert.equal(result.status,3);
    assert.match(result.stderr,/PUBLIC_TESTNET_ACCEPTANCE_BLOCKED_NO_REVIEWED_DEPLOYMENT/);
    assert.match(result.stderr,/local Anvil\/simulated journey still available/);
    assert.equal(existsSync(output),false);
  }finally{rmSync(tmp,{recursive:true,force:true});}
});
