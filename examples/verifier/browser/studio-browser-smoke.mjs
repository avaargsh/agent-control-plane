#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Real Chromium UX smoke. No wallet, no live RPC, no chain mutation.
import { chromium } from "playwright-core";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createVerifierServer } from "../server.mjs";
import { buildDemoCase } from '../public/demo-scenarios.mjs';

const assert=(condition,reason)=>{if(!condition)throw Error(reason)};
const browserCandidates=[
  process.env.AGENTLEDGER_CHROME_PATH,
  "/usr/bin/google-chrome","/usr/bin/chromium","/usr/bin/chromium-browser",
  "/opt/google/chrome/chrome",
].filter(Boolean);
const binary=browserCandidates.find(existsSync);
assert(binary,"No system Chromium/Chrome installed; browser acceptance must not silently skip");
const outputDir=resolve(process.env.AGENTLEDGER_BROWSER_ARTIFACTS || "../../out/demo-journey");
mkdirSync(outputDir,{recursive:true});
const server=createVerifierServer({
  lookup:async()=>{throw Error("browser simulation attempted an unauthorized RPC call");}
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
let browser;
try {
  browser=await chromium.launch({executablePath:binary,headless:true,
    args:["--no-sandbox","--disable-gpu"]});
  const context=await browser.newContext({
    viewport:{width:1440,height:900},acceptDownloads:true,reducedMotion:"reduce",
  });
  const page=await context.newPage();
  const errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  await page.goto("http://127.0.0.1:"+server.address().port+"/#proof-journey",
    {waitUntil:"networkidle"});
  assert((await page.locator("h1").innerText()).includes("Trust the evidence"),
    "Verification Studio heading absent");
  assert((await page.locator("#source-note").innerText()).includes("LOCAL SIMULATION"),
    "Simulation source label absent");

  await page.locator('[data-scenario="created"]').click();
  await page.waitForFunction(()=>document.querySelector("#chain-state")?.textContent==="Created");
  await page.locator('[data-scenario="paid"]').click();
  await page.waitForFunction(()=>document.querySelector("#chain-state")?.textContent==="Paid");
  assert(await page.locator("#payment-fact").innerText()==="PaymentUnknown",
    "Paid Recorder incorrectly promoted to verified payment");
  assert((await page.locator("#payment-nonce").innerText()).includes("NOT VERIFIED"),
    "missing nonce uncertainty");

  await page.locator('[data-scenario="proven"]').click();
  await page.waitForFunction(()=>document.querySelector("#chain-state")?.textContent==="Proven");
  await page.locator("#run-sample-proof").click();
  await page.waitForFunction(()=>
    document.querySelector("#verification-message")?.textContent?.includes("SIMULATED MATCH"));
  assert(await page.locator("#payment-fact").innerText()==="PaymentUnknown",
    "Evidence hash match improperly elevated payment");

  const summaryDownload=page.waitForEvent("download");
  await page.locator("#save-summary").click();
  const download=await summaryDownload;
  assert(download.suggestedFilename()==="summary.json","expected summary.json download");
  const report=JSON.parse(readFileSync(await download.path(),"utf8"));
  assert(report.mode==="LOCAL_SIMULATION","browser summary lost simulation classification");
  assert(report.task.descriptionHash?.length===66,"descriptionHash absent");
  assert(report.task.taskId?.length===66,"taskId absent");
  assert(report.evidence.verification==="HASH_MATCHED","browser Evidence digest unverified");
  assert(report.payment.observedState==="PaymentUnknown","browser falsely reports Paid");
  assert(report.payment.paymentNonce===null,"simulated payment nonce fabricated");
  assert(report.rpc.contacted===false,"simulation reports unexpected public RPC");
  assert(report.boundaries.publicTestnetAcceptance==="BLOCKED_NO_REVIEWED_DEPLOYMENT",
    "public acceptance must remain blocked");
  await page.screenshot({path:resolve(outputDir,"studio-desktop.png"),fullPage:true});

  await page.locator('[data-scenario="lost-ack"]').click();
  await page.waitForFunction(()=>document.querySelector("#scenario-heading")?.textContent==="Payment response missing");
  assert(await page.locator("#payment-fact").innerText()==="PaymentUnknown",
    "lost ACK did not remain PaymentUnknown");
  const lostSummary=page.waitForEvent("download");
  await page.locator("#save-summary").click();
  const lostReport=JSON.parse(readFileSync(await (await lostSummary).path(),"utf8"));
  assert(lostReport.payment.failureSemantic==="ACK_LOST_OUTCOME_UNKNOWN",
    "lost ACK summary does not record uncertainty");
  assert(lostReport.payment.chargeAgain===false,"lost ACK allowed recharge");

  await page.locator("#mode-live").click();
  assert((await page.locator("#source-note").innerText()).includes("PUBLIC TESTNET STATE ONLY"),
    "mode switch did not show source boundary");
  assert(await page.locator("#verify-button").isDisabled(),"stale simulated proof survived mode switch");
  await page.locator("#mode-demo").click();

  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:resolve(outputDir,"studio-mobile.png"),fullPage:true});
  const widths=await page.evaluate(()=>({
    scroll:document.documentElement.scrollWidth,
    viewport:document.documentElement.clientWidth,
  }));
  assert(widths.scroll<=widths.viewport+2,"mobile layout overflows horizontally");
  assert(await page.locator('.nav-items').isVisible(), 'Mobile site navigation must not be hidden');
  await page.locator('a.nav-item[href="#evidence-panel"]').click();
  assert(new URL(page.url()).hash==='#evidence-panel','Mobile Evidence Lab section navigation unavailable');
  assert((await page.locator('#guide-source').innerText()).includes('LOCAL SIMULATION'),'Source label absent before guide');
  assert(await page.evaluate(()=>getComputedStyle(document.querySelector('.director-actions')).position!=='fixed'),
    "inactive mobile guide must not cover page content");
  await page.locator('#guide-reset').click();
  await page.waitForFunction(()=>document.querySelector('#guide-counter').textContent.includes('1 / 7'));
  assert(await page.evaluate(()=>getComputedStyle(document.querySelector('.director-actions')).position==='fixed'),
    "active mobile guide controls should remain reachable");
  await page.locator('#guide-exit').click();
  assert(await page.evaluate(()=>getComputedStyle(document.querySelector('.director-actions')).position!=='fixed'),
    "exiting mobile guide must release fixed controls");
  assert(await page.locator('#guide-exit').isHidden(),"guide exit should hide after dismissal");
  // Complete presenter journey. Testnet reads below are explicitly mocked;
  // all local stages still use the real browser tool calculation/WebCrypto.
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#guide-reset').click();
  await page.waitForFunction(()=>document.querySelector('#guide-counter').textContent.includes('1 / 7'));
  await page.locator('#guide-next').click();
  await page.waitForFunction(()=>document.querySelector('#chain-state').textContent==='Paid');
  await page.locator('#guide-next').click();
  await page.waitForFunction(()=>document.querySelector('#chain-state').textContent==='Proven');
  assert(await page.locator('#tool-words').innerText()==='6','shared text tool calculation missing');
  await page.locator('#guide-next').click();
  await page.waitForFunction(()=>document.querySelector('#verification-message').textContent.includes('SIMULATED MATCH'));
  const evidenceDownload=page.waitForEvent('download');
  await page.locator('#download-evidence').click();
  const evidenceFile=await evidenceDownload;
  assert(evidenceFile.suggestedFilename()==='evidence.json','fixture Evidence filename');
  const sample=await buildDemoCase('proven');
  assert(JSON.parse(readFileSync(await evidenceFile.path(),'utf8')).inputSha256===sample.evidence.inputSha256,'downloaded fixture mismatch');
  await page.locator('#evidence-file').setInputFiles({name:'evidence.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(sample.evidence))});
  await page.locator('#input-file').setInputFiles({name:'input.txt',mimeType:'text/plain',buffer:Buffer.from(sample.inputBytes)});
  await page.locator('#output-file').setInputFiles({name:'output.json',mimeType:'application/json',buffer:Buffer.from(sample.outputBytes)});
  await page.locator('#verify-button').click();
  await page.waitForFunction(()=>document.querySelector('#verification-message').textContent.includes('Original input verified; output verified'));
  await page.locator('#guide-next').click();
  await page.waitForFunction(()=>document.querySelector('#verification-message').textContent.includes('EXPECTED REJECTION'));
  assert(await page.locator('#payment-fact').innerText()==='PaymentUnknown','tamper demonstration changed payment fact');
  await page.locator('#guide-next').click();
  await page.waitForFunction(()=>document.querySelector('#scenario-heading').textContent==='Payment response missing');
  await page.waitForFunction(()=>document.querySelector('#guide-result').textContent.includes('本地六步演示已完成'));
  assert((await page.locator('#guide-next').innerText()).includes('可选'),'Public Testnet optional step not distinguished');
  const reference=JSON.parse(readFileSync(new URL('../../../phase0/demo-showcase.json',import.meta.url),'utf8'));
  const fixture={...sample.task,status:'Created',taskId:reference.taskId,contract:reference.contract};
  await page.route(/\/api\/task\?/,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(fixture)}));
  await page.route(/\/api\/task-creation\?/,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({state:'CONFIRMED',chainId:10143,contract:reference.contract,taskId:reference.taskId,transactionHash:reference.creationTransaction,from:fixture.creator,task:fixture,blockNumber:'100'})}));
  await page.locator('#guide-next').click();
  await page.waitForFunction(()=>document.querySelector('#guide-result').textContent.includes('完整导览完成'));
  assert((await page.locator('#guide-source').innerText()).includes('OPTIONAL TESTNET'),'Live-source guide label absent');
  assert(await page.locator('#chain-state').innerText()==='Created','mocked live guide result missing');
  assert(await page.locator('#creation-transaction').isVisible(),'creation provenance link missing');
  assert((await page.locator('#share-url').inputValue()).includes(reference.taskId),'live share link missing task identity');

  // A delayed local file read must not turn a newly selected mode into a match.
  await page.locator('#mode-demo').click();
  await page.locator('[data-scenario="proven"]').click();
  await page.waitForFunction(()=>document.querySelector('#chain-state').textContent==='Proven');
  await page.locator('#evidence-file').setInputFiles({name:'evidence.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(sample.evidence))});
  await page.evaluate(()=>{
    const original=File.prototype.arrayBuffer;
    File.prototype.arrayBuffer=function(){const file=this;return new Promise(resolve=>{window.releaseDelayedEvidence=async()=>{File.prototype.arrayBuffer=original;resolve(await original.call(file));};});};
  });
  await page.locator('#verify-button').click();
  await page.waitForFunction(()=>typeof window.releaseDelayedEvidence==='function');
  await page.locator('#mode-live').click();
  await page.evaluate(()=>window.releaseDelayedEvidence());
  assert(await page.locator('#evidence-fact').innerText()==='Not verified','stale local proof contaminated a live observation');
  assert(await page.locator('#save-summary').isDisabled(),'empty live observation can export stale task');
  assert(await page.locator('#chain-flag').innerText()==='NOT LOADED','stale provenance flag remained');

  // A mismatched identity is an error, never a fallback to a successful fixture.
  await page.unroute(/\/api\/task\?/);
  await page.route(/\/api\/task\?/,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...fixture,taskId:'0x'+'ff'.repeat(32)})}));
  await page.locator('#contract').fill(reference.contract);await page.locator('#taskId').fill(reference.taskId);
  await page.locator('#query-button').click();
  await page.waitForFunction(()=>document.querySelector('#query-message').textContent.includes('identity mismatch'));
  assert(await page.locator('#chain-state').innerText()==='Not loaded','wrong identity presented as loaded');

  // A failed optional Testnet read never reverses the completed offline proof journey.
  await page.route(/\\/api\\/showcase/,route=>route.fulfill({status:503,contentType:'application/json',body:'{}'}));
  await page.locator('#guide-reset').click();
  for(let index=1;index<6;index++) await page.locator('#guide-next').click();
  await page.waitForFunction(()=>document.querySelector('#guide-result').textContent.includes('本地六步演示已完成'));
  await page.locator('#guide-next').click();
  await page.waitForFunction(()=>document.querySelector('#guide-result').textContent.includes('可选 Testnet 只读查询未成功'));
  assert(await page.locator('#payment-fact').innerText()==='PaymentUnknown','Unavailable public RPC changed payment status');
  assert(await page.locator('#save-summary').isDisabled(),'Unavailable live task can export a fake receipt');
  assert(errors.length===0,"browser page errors: "+errors.join("; "));
  console.log("CHROMIUM STUDIO PASS: seven-step guide (live lookup mocked), real local text calculation/WebCrypto, downloaded/reuploaded Evidence, tamper rejection, stale-read isolation, identity mismatch, PaymentUnknown, responsive mobile. No public RPC or signing.");
}finally{
  if(browser)await browser.close();
  await new Promise(resolve=>server.close(resolve));
}
