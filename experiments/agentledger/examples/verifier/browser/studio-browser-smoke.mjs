#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Real Chromium UX smoke. No wallet, no live RPC, no chain mutation.
import { chromium } from "playwright-core";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createVerifierServer } from "../server.mjs";

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
  assert(errors.length===0,"browser page errors: "+errors.join("; "));
  console.log("CHROMIUM STUDIO PASS: Created→Paid claim→Evidence→Verify, PaymentUnknown, downloaded summary.json, responsive mobile");
}finally{
  if(browser)await browser.close();
  await new Promise(resolve=>server.close(resolve));
}
