#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Graphical Expo Web smoke against the *exported* app (not merely typecheck).
// Simulated journey uses no Monad RPC, no wallet, no chain writes.
import {createServer} from "node:http";
import {readFile,stat,mkdir} from "node:fs/promises";
import {existsSync} from "node:fs";
import {join,extname,resolve,sep} from "node:path";
import {chromium} from "playwright-core";
import {createHash} from "node:crypto";

const root=resolve("dist");
const output=resolve("../../out/pocket-browser");
const chrome=[
 process.env.AGENTLEDGER_CHROME_PATH,
 "/usr/bin/google-chrome","/usr/bin/chromium","/usr/bin/chromium-browser",
].filter(Boolean).find(existsSync);
if(!existsSync(join(root,"index.html")))throw Error("Missing Expo Web export. Run npm run export:web");
if(!chrome)throw Error("Chrome/Chromium missing. Cannot waive browser smoke");
const contentTypes={".html":"text/html; charset=utf-8",".js":"application/javascript; charset=utf-8",
 ".json":"application/json",".css":"text/css; charset=utf-8",".png":"image/png",
 ".svg":"image/svg+xml",".woff2":"font/woff2"};
const http=createServer(async(req,res)=>{
 const path=new URL(req.url||"/","http://127.0.0.1").pathname;
 const target=resolve(root,"."+decodeURIComponent(path==="/"?"/index.html":path));
 if(target!==root&&!target.startsWith(root+sep)){res.writeHead(404).end();return;}
 try{
  const info=await stat(target);
  const file=info.isDirectory()?join(target,"index.html"):target;
  res.writeHead(200,{"content-type":contentTypes[extname(file)]||"application/octet-stream"});
  res.end(await readFile(file));
 }catch{res.writeHead(404).end();}
});
await mkdir(output,{recursive:true});
await new Promise(r=>http.listen(0,"127.0.0.1",r));
let browser;
try{
 browser=await chromium.launch({headless:true,executablePath:chrome,
  args:["--no-sandbox","--disable-gpu"]});
 const context=await browser.newContext({viewport:{width:1360,height:900}});
 const page=await context.newPage();
 const errors=[];
 page.on("pageerror",e=>errors.push(e.message));
 const origin="http://127.0.0.1:"+http.address().port;
 await page.goto(origin,{waitUntil:"networkidle"});
 await page.getByText("Your agent tasks",{exact:true}).waitFor();
 await page.getByText("JUDGE / EVIDENCE VIEW",{exact:true}).waitFor();
 // New judge-facing deterministic flow, matching slides/judge.md.
 await page.getByRole("button",{name:/Start 3-minute guided demo/}).click();
 await page.getByText("GUIDED DEMO · LOCAL SIMULATION ONLY").waitFor();
 for(const expected of ["02 · Recorder Paid claim","03 · Evidence / Verify","04 · Lost ACK / No recharge"]){
   await page.getByRole("button",{name:"Guide next"}).click();
   await page.getByText(expected,{exact:true}).waitFor();
   await page.getByText(/PaymentUnknown/).first().waitFor();
 }
 if(!await page.getByRole("button",{name:"Guide next"}).isDisabled())
   throw Error("Guided demonstration ran past no-recharge endpoint");
 await page.getByRole("button",{name:"Guide back"}).click();
 await page.getByText("03 · Evidence / Verify",{exact:true}).waitFor();
 await page.getByRole("button",{name:"Guide next"}).click();
 // Close guide; restore familiar Task tab for the existing regression suite.
 await page.getByRole("button",{name:"Guide back"}).click();
 await page.getByRole("button",{name:"Guide back"}).click();
 await page.getByRole("button",{name:"Guide back"}).click();
 await page.getByText("01 · Created",{exact:true}).waitFor();
 await page.getByRole("button",{name:"Tasks"}).first().catch(()=>{});
 await page.locator('[aria-label="Local simulated guided demonstration"]').getByRole("button",{name:"×"}).click().catch(async()=>{
   await page.locator('[aria-label="Local simulated guided demonstration"]').locator('button').first().click();
 });

 await page.getByText("Paid claim",{exact:true}).first().click();
 await page.getByText("PaymentUnknown",{exact:false}).first().waitFor();
 await page.getByText("Evidence",{exact:true}).first().click();
 await page.getByRole("button",{name:"Verify SHA-256 locally"}).click();
 await page.getByText("SIMULATED HASH MATCH · VERIFIED BY SHA-256").waitFor();
 // Drive the actual Expo Web OS-style DocumentPicker, not a synthetic
 // state setter. A tampered ORIGINAL binary input must revoke verification.
 async function attach(kind,name,mimeType,buffer){
   const field=kind==="Evidence JSON file"?"EVIDENCE":kind==="original input file"?"INPUT":"OUTPUT";
   const label=field==="EVIDENCE"?/^(Choose Evidence JSON file|Evidence:)/i:
     field==="INPUT"?/^(Choose original input file|Input:)/i:
     /^(Choose original output file|Output:)/i;
   const button=page.getByRole("button",{name:label});
   await button.click();
   // Expo's Web picker dispatches a synthetic click on a dynamically
   // attached hidden input. Headless Chromium need not emit a native
   // filechooser event for that click. Fill the actual file input
   // created by Expo rather than mocking the app's state or picker.
   // Verify the persistent Web DOM input associated with the clicked
   // picker action, never an incidental hidden input of another kind.
   const pickerInput=page.locator('input[type="file"][data-kind="'+field.toLowerCase()+'"]');
   await pickerInput.setInputFiles({name,mimeType,buffer:Buffer.from(buffer)},{timeout:7000});
   // File.read() and state commit are asynchronous; wait for the
   // production UI acknowledgment before asserting proof invalidation.
   try{
     await page.getByText(field+"_FILE_LOADED_LOCAL_ONLY").waitFor({timeout:7000});
   }catch{
     const visible=await page.locator("body").innerText();
     const inputs=await page.locator('input[type="file"]').count();
     throw Error("FILE_PICKER_UI_NOT_ACKNOWLEDGED: "+field+"; DOM inputs="+inputs+"; UI="+visible.slice(-2300));
   }
 }
 await attach("original input file","tampered.bin","application/octet-stream","tampered binary input");
 await page.getByRole("button",{name:"Verify SHA-256 locally"}).click();
 await page.getByText(/Input bytes SHA-256 mismatch/).waitFor();
 await attach("original input file","original.bin","application/octet-stream",
   "Demo input only: summarize local evidence.\n");
 await page.getByRole("button",{name:"Verify SHA-256 locally"}).click();
 await page.getByText("SIMULATED HASH MATCH · VERIFIED BY SHA-256").waitFor();
 const rawInput="Demo input only: summarize local evidence.\n";
 const rawOutput="Local summary: evidence is inspectable, payment unverified.\n";
 const digest=value=>"0x"+createHash("sha256").update(value).digest("hex");
 const evidence={
   completedAt:"2026-10-16T00:00:00.000Z",executor:"local-fixture",
   inputSha256:digest(rawInput),modelOrTool:"deterministic-demo",
   outputSha256:digest(rawOutput),schemaVersion:"agentledger.evidence.v1",
   taskId:"0x"+"23".repeat(32)
 };
 await attach("Evidence JSON file","evidence.json","application/json",JSON.stringify(evidence));
 await page.getByRole("button",{name:"Verify SHA-256 locally"}).click();
 await page.getByText("SIMULATED HASH MATCH · VERIFIED BY SHA-256").waitFor();
 // Replacing a VERIFIED source with malformed JSON MUST revoke the old
 // success *before* any retry; the previous file cannot be silently reused.
 await page.getByRole("button",{name:/^(Evidence:|Choose Evidence JSON file)/}).click();
 await page.locator('input[type="file"][data-kind="evidence"]').setInputFiles({
   name:"broken.json",mimeType:"application/json",buffer:Buffer.from("{invalid"),
 });
 await page.getByText(/FILE_NOT_VERIFIED:/).waitFor();
 await page.getByText("NOT VERIFIED",{exact:true}).first().waitFor();
 if(!await page.getByRole("button",{name:"Verify SHA-256 locally"}).isDisabled())
   throw Error("Malformed Evidence replacement left old proof reusable");
 await attach("Evidence JSON file","evidence.json","application/json",JSON.stringify(evidence));
 await page.getByRole("button",{name:"Verify SHA-256 locally"}).click();
 await page.getByText("SIMULATED HASH MATCH · VERIFIED BY SHA-256").waitFor();
 // Download the same provenance-constrained report seen in the desktop
 // inspector. A hash match MUST NOT make payment Paid or Testnet accepted.
 const downloadPromise=page.waitForEvent("download");
 await page.getByRole("button",{name:"Export summary.json"}).click();
 const download=await downloadPromise;
 if(download.suggestedFilename()!=="summary.json")throw Error("Wrong report download name");
 const summary=JSON.parse(await readFile(await download.path(),"utf8"));
 if(summary.mode!=="LOCAL_SIMULATION" ||
    summary.evidence.verification!=="SIMULATED_HASH_MATCHED_TO_PINNED_REFERENCE" ||
    summary.payment.observed!=="PaymentUnknown" ||
    summary.payment.chargeAgain!==false ||
    summary.rpc.contacted!==false ||
    summary.security.publicTestnetAcceptance!=="BLOCKED_NO_REVIEWED_DEPLOYMENT"){
   throw Error("Exported report incorrectly promotes simulated payment or source");
 }
 // Preserve separate evidence and overview shots for product review;
 // the browser test must show what users actually interacted with.
 await page.screenshot({path:join(output,"pocket-evidence.png"),fullPage:true});
 await page.getByRole("tab",{name:"Payment"}).click();
 await page.getByText("PaymentUnknown",{exact:true}).first().waitFor();
 await page.getByRole("button",{name:/PaymentUnknown \/ Lost ACK/}).click();
 await page.getByText("PaymentUnknown",{exact:true}).first().waitFor();
 await page.getByRole("tab",{name:"Tasks"}).click();
 await page.getByText("Your agent tasks",{exact:true}).waitFor();
 await page.evaluate(()=>{document.querySelectorAll("*").forEach(el=>{if(el.scrollTop>0)el.scrollTop=0;});});
 await page.screenshot({path:join(output,"pocket-desktop.png"),fullPage:true});
 await page.getByLabel("Ledger contract").fill("invalid");
 await page.getByLabel("Task ID").fill("invalid");
 await page.getByRole("button",{name:/Observe Task/}).click();
 await page.getByText(/INVALID_TASK_LOOKUP/).waitFor();
 // Browser-level read-only task flow. Intercept the exact GET boundary:
 // no public Monad RPC, wallet, receipt or contract write is contacted.
 const ledger="0x"+"42".repeat(20),taskId="0x"+"23".repeat(32);
 const liveTask={
   chainId:10143,contract:ledger,taskId,
   status:"Proven",creator:"0x"+"64".repeat(20),payer:"0x"+"75".repeat(20),
   amountAtomic:"1000",maxBudgetAtomic:"2000",
   descriptionHash:"0x"+"a1".repeat(32),
   evidenceHash:digest(JSON.stringify(evidence)),
   settlementRef:"0x"+"8a".repeat(32),
 };
 let taskReads=0,wrongIdentity=false;
 await page.route("**/api/task?**",async route=>{
   const request=route.request(),url=new URL(request.url());
   if(request.method()!=="GET" || url.searchParams.get("contract")!==ledger ||
      url.searchParams.get("taskId")!==taskId){
     throw Error("Unexpected task-only RPC request");
   }
   taskReads++;
   const body=wrongIdentity?{...liveTask,contract:"0x"+"ff".repeat(20)}:liveTask;
   await route.fulfill({
     status:200,contentType:"application/json",
     headers:{"access-control-allow-origin":origin,"vary":"Origin"},
     body:JSON.stringify(body),
   });
 });
 await page.getByLabel("Ledger contract").fill(ledger);
 await page.getByLabel("Task ID").fill(taskId);
 await page.getByRole("button",{name:/Observe Task/}).click();
 await page.getByText(/TASK OBSERVED · PaymentUnknown/).waitFor();
 await page.getByRole("tab",{name:"Evidence"}).click();
 await page.getByLabel("Evidence JSON").fill(JSON.stringify(evidence));
 await page.getByRole("button",{name:"Verify SHA-256 locally"}).click();
 await page.getByText("HASH MATCHED · PAY UNKNOWN").waitFor();
 await page.getByRole("tab",{name:"Journal"}).click();
 const [liveDownload]=await Promise.all([
   page.waitForEvent("download",{timeout:8000}),
   // Journal exposes the same export action as the desktop judge panel.
   // Target the phone's first control, not an ambiguous multi-match.
   page.getByRole("button",{name:"Export summary.json"}).first().click(),
 ]);
 const liveReport=JSON.parse(await readFile(await liveDownload.path(),"utf8"));
 if(liveReport.mode!=="TESTNET_TASK_READ_ONLY" ||
   liveReport.payment.observed!=="PaymentUnknown" ||
   liveReport.payment.chargeAgain!==false ||
   liveReport.evidence.verification!=="HASH_MATCHED_EVIDENCE_ONLY" ||
   liveReport.security.publicTestnetAcceptance!=="BLOCKED_NO_REVIEWED_DEPLOYMENT" ||
   liveReport.security.realFundsUsed!==false) {
   throw Error("Task-only report was incorrectly upgraded to paid Testnet acceptance");
 }
 if(taskReads!==1)throw Error("Expected exactly one read-only HTTP task fetch");
 // Same task ID plus wrong Ledger address: reject, and revoke the old
 // observed-task context and Evidence match, not just show a warning.
 await page.getByRole("tab",{name:"Tasks"}).click();
 wrongIdentity=true;
 await page.getByRole("button",{name:/Observe Task/}).click();
 await page.getByText(/CHAIN_TASK_IDENTITY_MISMATCH/).waitFor();
 await page.getByRole("tab",{name:"Evidence"}).click();
 if(await page.getByText("HASH MATCHED · PAY UNKNOWN").count())
   throw Error("Stale successful Evidence from rejected chain identity");
 await page.getByRole("tab",{name:"Tasks"}).click();
 await page.setViewportSize({width:390,height:844});
 await page.waitForFunction(()=>!document.body.innerText.includes("JUDGE / EVIDENCE VIEW"),null,{timeout:10000});
 await page.getByText("Your agent tasks",{exact:true}).waitFor();
 const sizes=await page.evaluate(()=>({
   overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,
   offenders:[...document.querySelectorAll("*")]
     .map(el=>({tag:el.tagName,txt:el.textContent?.slice(0,45)||"",
       right:Math.round(el.getBoundingClientRect().right)}))
     .filter(item=>item.right>innerWidth+3).slice(0,8)
 }));
 if(sizes.overflow>3)throw Error("Horizontal mobile overflow "+JSON.stringify(sizes));
 await page.screenshot({path:join(output,"pocket-mobile.png"),fullPage:true});
 if(errors.length)throw Error("Browser exceptions: "+errors.join("; "));
 console.log("POCKET WEB PASS: Created → Recorder Paid → Evidence real SHA-256 → PaymentUnknown Lost ACK; unsafe lookup rejected; mobile layout okay");
}finally{
 if(browser)await browser.close();
 await new Promise(r=>http.close(r));
}
