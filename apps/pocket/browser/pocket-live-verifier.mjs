#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Live READ-ONLY Testnet acceptance for the exported Pocket app.
//
// Unlike pocket-browser-smoke.mjs (which fulfills /api/task inside Chromium),
// this script starts the real loopback verifier service and lets the app
// fetch the REAL recorded Monad Testnet task through it. No wallet, no
// signing, no transaction, no reviewed-deployment registry change and no
// payment acceptance is produced by this script; every claim stays
// TESTNET_TASK_READ_ONLY / PaymentUnknown.
//
// Run from apps/pocket after a fresh `npm run export:web`:
//   npm run test:browser:live
//
// Environment:
//   AGENTLEDGER_POCKET_VERIFIER_PORT  verifier port (default 4321, the app default)
//   MONAD_RPC_URL                     official Monad Testnet RPC (default in task-reader)
//   AGENTLEDGER_CHROME_CMD            explicit Chrome/Chromium executable
import {spawn} from "node:child_process";
import {createServer} from "node:http";
import {mkdir, readFile, writeFile, rm} from "node:fs/promises";
import {existsSync} from "node:fs";
import {join, extname, resolve} from "node:path";
import {chromium} from "playwright-core";

const CONTRACT="0x2a497095a6f3b6675e251432d50060d0a6fc1b9c";
const TASK_ID="0xd424d23731f1c390a22b1e7f9cafdbe84d1dc0753b714c391b735f1e9139fb0e";
const CREATION_TX="0x40146d20bedda88a4dbdb118aa1cdae421e32f3c77271744d6d0a60db3d675c6";
const DEPLOYMENT_TX="0x4565654b2e3a6f32329b005b0de779a6efb91806e1bd7a2582c7532ce3bcdfbe";
// A well-formed but codeless address exercises the real fail-closed path.
const WRONG_LEDGER="0x"+"42".repeat(20);

const dist=resolve("dist");
if(!existsSync(join(dist,"index.html")))
  throw Error("Missing Expo Web export. Run: npm run export:web");
const repoRoot=resolve("../..");
const verifierEntry=join(repoRoot,"examples/verifier/server.mjs");
if(!existsSync(verifierEntry))throw Error("examples/verifier/server.mjs missing");

const verifierPort=Number(process.env.AGENTLEDGER_POCKET_VERIFIER_PORT||4321);
const webPort=19006; // one of the verifier's explicit Pocket Web CORS origins
const verifierOrigin="http://127.0.0.1:"+verifierPort;
const webOrigin="http://127.0.0.1:"+webPort;
const output=resolve("../../out/pocket-live-testnet");
const joinArtifact=name=>join(output,name);

const started=Date.now();
const WATCHDOG_MS=150000;
let stepLabel="startup";
const watchdog=setTimeout(()=>{
  console.error("POCKET LIVE READ-ONLY STUCK at step:",stepLabel,
    " after",Math.round((Date.now()-started)/1000)+"s");
  process.exit(124);
},WATCHDOG_MS);
function step(label){stepLabel=label;console.log("[live:",label+"]",Math.round((Date.now()-started)/1000)+"s");}

// A crashed run must never leave a previous observation masked as new.
await mkdir(output,{recursive:true});
for(const stale of ["pocket-live-acceptance.json","pocket-live-summary.json"])
  await rm(joinArtifact(stale),{force:true});

// --- 1. Start the real verifier (read-only, loopback only) -------------------
const verifierEnv={...process.env,PORT:String(verifierPort)};
delete verifierEnv.EXPO_PUBLIC_VERIFIER_URL;
const verifier=spawn(process.execPath,[verifierEntry],{
  cwd:repoRoot,env:verifierEnv,stdio:["ignore","pipe","pipe"],
});
let verifierLog="";
verifier.stdout.on("data",d=>verifierLog+=d);
verifier.stderr.on("data",d=>verifierLog+=d);
verifier.on("error",error=>{verifierLog+=String(error);});
let web;
let browser;
try{
  step("verifier-bootstrap");
  await verifierReady();
  async function verifierReady(){
    for(let i=0;i<40;i++){
      if(verifier.exitCode!==null)throw Error("Verifier exited: "+verifierLog.slice(-400));
      try{
        const res=await fetch(verifierOrigin+"/api/healthz");
        if(res.status===200){
          const health=await res.json();
          if(health.ok===true)break;
        }
      }catch{/* verifier not listening yet */}
      if(i===39)throw Error("Verifier never became ready: "+verifierLog.slice(-400));
      await new Promise(r=>setTimeout(r,250));
    }
  }

  // --- 2. Server-side real task reconciliation (raw evidence) --------------
  step("server-side-reconcile");
  const direct=new URL("/api/task",verifierOrigin);
  direct.searchParams.set("contract",CONTRACT);direct.searchParams.set("taskId",TASK_ID);
  const reconciled=await (await fetch(direct)).json();
  if(reconciled.chainId!==10143||reconciled.status!=="Created"
    ||reconciled.amountAtomic!=="0")
    throw Error("Real task on-chain state is not the expected Created/PaymentUnknown baseline: "
      +JSON.stringify(reconciled).slice(0,200));

  // --- 3. Serve the exported Pocket bundle on its whitelisted origin -------
  step("serve-exported-app");
  const contentTypes={".html":"text/html; charset=utf-8",".js":"application/javascript; charset=utf-8",
    ".json":"application/json",".css":"text/css; charset=utf-8",".png":"image/png",".svg":"image/svg+xml"};
  web=createServer(async(req,res)=>{
    const path=new URL(req.url||"/",webOrigin).pathname;
    const target=resolve(dist,"."+decodeURIComponent(path==="/"?"/index.html":path));
    if(target!==dist&&!target.startsWith(dist+"/")){res.writeHead(404).end();return;}
    try{
      const body=await readFile(target);
      res.writeHead(200,{"content-type":contentTypes[extname(target)]||"application/octet-stream"});
      res.end(body);
    }catch{
      if(!res.headersSent)res.writeHead(404).end();
    }
  });
  await new Promise((ready,deny)=>
    web.listen(webPort,"127.0.0.1",ready).on("error",deny));

  // --- 4. Drive the real app against the real chain ------------------------
  step("launch-browser");
  const chrome=process.env.AGENTLEDGER_CHROME_CMD
    ??["/usr/bin/google-chrome","/usr/bin/chromium","/usr/bin/chromium-browser"]
      .find(existsSync);
  if(!chrome)throw Error("Chrome/Chromium missing");
  browser=await chromium.launch({headless:true,executablePath:chrome,
    args:["--no-sandbox","--disable-gpu"]});
  let realGetCount=0;
  try{
    const context=await browser.newContext({viewport:{width:1360,height:900},
      accept_downloads:true});
    const page=await context.newPage();
    const errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    page.on("request",request=>{
      if(request.url().startsWith(verifierOrigin+"/api/task"))realGetCount++;
    });
    step("app-load");
    await page.goto(webOrigin,{waitUntil:"networkidle"});
    await page.getByText("Your agent tasks",{exact:true}).waitFor({timeout:20000});

    // The recorded Testnet task is observed through the app's GET-only adapter.
    step("observe-real-task");
    await page.getByLabel("Ledger contract").fill(CONTRACT);
    await page.getByLabel("Task ID").fill(TASK_ID);
    await page.getByRole("button",{name:/Observe Task/}).click();
    await page.getByText(/TASK OBSERVED · PaymentUnknown/).waitFor({timeout:30000});
    await page.getByText("TESTNET · TASK OBSERVED").waitFor({timeout:10000});
    await page.getByText("Observed Task").waitFor({timeout:10000});

    // Inspector must stay an untrusted observation, never acceptance.
    step("inspector-boundary");
    await page.getByText("TESTNET TASK ONLY · PAYMENT UNKNOWN").waitFor({timeout:10000});
    await page.getByText("10143 (task read)").waitFor({timeout:10000});

    // Evidence for a Created task: digest displayed, verification NOT_VERIFIED,
    // verify button disabled (no Proven anchor to match yet).
    step("evidence-unverified");
    await page.getByRole("tab",{name:"Evidence"}).click();
    await page.getByText("SHA-256 anchor").waitFor({timeout:10000});
    if(!await page.getByText("NOT VERIFIED",{exact:true}).first().isVisible().catch(()=>false))
      throw Error("Evidence report claimed verification without a Proven anchor");
    if(!await page.getByRole("button",{name:"Verify SHA-256 locally"}).isDisabled())
      throw Error("Verify enabled although the real task is only Created");

    // Payment tab: PaymentUnknown stands; Recorder claim stays NotRecorded.
    step("payment-unknown");
    await page.getByRole("tab",{name:"Payment"}).click();
    await page.getByText("PaymentUnknown",{exact:true}).first().waitFor({timeout:10000});
    await page.getByText(/Recorder claim: NotRecorded/).waitFor({timeout:10000});
    await page.getByRole("button",{name:/PaymentUnknown \/ Lost ACK/}).click();
    await page.getByText("PaymentUnknown",{exact:true}).first().waitFor({timeout:10000});

    // Observing a codeless Ledger with the same task ID must fail closed and
    // revoke the observed context via the verifier's genuine 502 path.
    step("wrong-ledger-fail-closed");
    await page.getByRole("tab",{name:"Tasks"}).click();
    await page.getByLabel("Ledger contract").fill(WRONG_LEDGER);
    await page.getByRole("button",{name:/Observe Task/}).click();
    await page.getByText(/NOT VERIFIED: TASK_READ_UNAVAILABLE_/).waitFor({timeout:30000});
    await page.getByText("LOCAL DEMONSTRATION").waitFor({timeout:10000});

    // Restore the real observation and download its summary.json.
    step("summary-download");
    await page.getByLabel("Ledger contract").fill(CONTRACT);
    await page.getByRole("button",{name:/Observe Task/}).click();
    await page.getByText(/TASK OBSERVED · PaymentUnknown/).waitFor({timeout:30000});
    await page.screenshot({path:joinArtifact("pocket-live-tasks-observed.png"),fullPage:true});
    await page.getByRole("tab",{name:"Journal"}).click();
    const [download]=await Promise.all([
      page.waitForEvent("download",{timeout:10000}),
      page.getByRole("button",{name:"Export summary.json"}).first().click(),
    ]);
    if(download.suggestedFilename()!=="summary.json")
      throw Error("Unexpected report filename: "+download.suggestedFilename());
    await download.saveAs(joinArtifact("pocket-live-summary.json"));
    const summary=JSON.parse(await readFile(joinArtifact("pocket-live-summary.json"),"utf8"));
    expectSummaryShape(summary);

    // Mobile layout re-mounts thePhone (desktop split → single column resets
    // component state), so the real task is observed once more at 390px.
    step("mobile-layout");
    await page.setViewportSize({width:390,height:844});
    await page.getByText("Your agent tasks",{exact:true}).waitFor({timeout:20000});
    await page.getByLabel("Ledger contract").fill(CONTRACT);
    await page.getByLabel("Task ID").fill(TASK_ID);
    await page.getByRole("button",{name:/Observe Task/}).click();
    await page.getByText(/TASK OBSERVED · PaymentUnknown/).waitFor({timeout:30000});
    await page.screenshot({path:joinArtifact("pocket-live-mobile.png"),fullPage:true});

    if(errors.length)throw Error("Browser exceptions: "+errors.join("; "));
    // One GET per deliberate observe attempt: reconcile restore + mobile
    // re-observe, plus one failed wrong-Ledger attempt inside the page.
    if(realGetCount!==4)throw Error("Unexpected verifier traffic count: "+realGetCount);

    // --- 5. Secret-free acceptance record ---------------------------------
    step("record");
    const record=buildRecord();
    await writeFile(joinArtifact("pocket-live-acceptance.json"),
      JSON.stringify(record,null,2)+"\n",{flag:"wx"});
    clearTimeout(watchdog);
    console.log("POCKET LIVE READ-ONLY PASS: real Monad Testnet task observed via app:"
      +" Created / PaymentUnknown, wrong-Ledger lookup failed closed,"
      +" summary.json stayed TESTNET_TASK_READ_ONLY; artifacts in out/pocket-live-testnet");
    function buildRecord(){
      return {
        schemaVersion:"agentledger.pocket.live-acceptance.v1",
        observedAt:new Date().toISOString(),
        classification:"TESTNET_TASK_READ_ONLY_OBSERVATION_NOT_PAYMENT_ACCEPTANCE",
        boundaries:{finalityProven:false,serviceDeliveryProven:false,
          paymentAccepted:false,usdCTransferIndependentlyVerified:false,
          reviewedDeployment:false,walletUsed:false,transactionsBroadcast:0,
          verdict:"PASS_OBSERVED_CREATED_PAYMENT_UNKNOWN"},
        verifier:{origin:verifierOrigin,readOnly:true,
          rpcPinned:process.env.MONAD_RPC_URL||"https://testnet-rpc.monad.xyz"},
        web:{origin:webOrigin,export:"apps/pocket/dist (npm run export:web)"},
        ledger:{chainId:10143,contract:CONTRACT,taskId:TASK_ID,
          creationTransaction:CREATION_TX,deploymentTransaction:DEPLOYMENT_TX},
        serverSideReconcile:{status:reconciled.status,
          amountAtomic:reconciled.amountAtomic,
          maxBudgetAtomic:reconciled.maxBudgetAtomic,
          descriptionHash:reconciled.descriptionHash,
          evidenceHash:reconciled.evidenceHash,creator:reconciled.creator,
          payer:reconciled.payer,expectedPayment:"PaymentUnknown",
          chargeAgain:false},
        ui:{taskObserved:"Created",inspector:"TESTNET_TASK_ONLY",
          evidenceVerification:"NOT_VERIFIED",verifyButtonDisabled:true,
          paymentObserved:"PaymentUnknown",recorderClaim:"NotRecorded",
          wrongLedgerRejected:"TASK_READ_UNAVAILABLE from empty bytecode",
          observedContextRevokedOnFailure:true,summaryDownloaded:true,
          mobileObserved:true,
          verifierTaskReads:realGetCount,expectedVerifierTaskReads:4},
        payment:{observed:"PaymentUnknown",chargeAgain:false,
          publicTestnetAcceptance:"BLOCKED_NO_REVIEWED_DEPLOYMENT"},
        summaryRef:"pocket-live-summary.json",
        screenshots:["pocket-live-tasks-observed.png","pocket-live-mobile.png"],
      };
    }
    function expectSummaryShape(summary){
      if(summary.schemaVersion!=="agentledger.pocket.summary.v1"
        ||summary.mode!=="TESTNET_TASK_READ_ONLY"
        ||summary.outcome!=="TASK_OBSERVED_NOT_PAYMENT_ACCEPTANCE"
        ||summary.task.taskId!==TASK_ID.toLowerCase()
        ||summary.task.descriptionHash!==reconciled.descriptionHash
        ||summary.task.ledgerState!=="Created"
        ||summary.chainId!==10143
        ||summary.contract.toLowerCase()!==CONTRACT.toLowerCase()
        ||summary.rpc.contacted!==true
        ||summary.payment.observed!=="PaymentUnknown"
        ||summary.payment.recorderClaim!=="NotRecorded"
        ||summary.payment.chargeAgain!==false
        ||summary.evidence.evidenceHash!=="0x"+"0".repeat(64)
        ||summary.evidence.verification!=="NOT_VERIFIED"
        ||summary.evidence.executionCorrectnessVerified!==false
        ||summary.security.publicTestnetAcceptance!=="BLOCKED_NO_REVIEWED_DEPLOYMENT"
        ||summary.security.mainnetAccepted!==false
        ||summary.security.realFundsUsed!==false)
        throw Error("Downloaded summary.json misrepresents the read-only observation: "
          +JSON.stringify(summary).slice(0,240));
    }
  }finally{if(browser)await browser.close().catch(()=>{});}
}catch(error){
  clearTimeout(watchdog);
  console.error("POCKET LIVE READ-ONLY FAILED:",error.message);
  console.error(verifierLog.slice(-800));
  process.exitCode=1;
}finally{
  clearTimeout(watchdog);
  if(web){await new Promise(r=>web.close(r)).catch(()=>{});}
  if(verifier.exitCode===null){
    verifier.once("exit",()=>process.exit(process.exitCode||0));
    verifier.kill("SIGTERM");
    setTimeout(()=>process.exit(process.exitCode||1),4000).unref();
  }
}
