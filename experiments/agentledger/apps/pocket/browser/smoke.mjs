// SPDX-License-Identifier: Apache-2.0
// Actual built Expo Web bundle exercised with local-only headless Chromium.
// No wallet, real RPC, account creation, external service, or Testnet payment.
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const dist=resolve(fileURLToPath(new URL("../dist/",import.meta.url)));
const out=fileURLToPath(new URL("../e2e-artifacts/",import.meta.url));
await mkdir(out,{recursive:true});
const exists=existsSync(join(dist,"index.html"));
if(!exists)throw Error("Run npm run build:web before browser smoke");
const types={".html":"text/html",".js":"text/javascript",".json":"application/json",
  ".css":"text/css",".png":"image/png",".woff2":"font/woff2"};
const server=createServer(async(req,res)=>{
  if(req.method!=="GET"){res.writeHead(405);res.end("Read-only");return;}
  try{
    const u=new URL(req.url,"http://localhost");
    const target=resolve(dist,"."+decodeURIComponent(u.pathname==="/"?"/index.html":u.pathname));
    if(target!==join(dist,"index.html")&&!target.startsWith(dist+sep)){
      res.writeHead(403);res.end();return;
    }
    const content=await readFile(target);
    res.writeHead(200,{"content-type":types[extname(target)]||"application/octet-stream",
      "cache-control":"no-store","x-content-type-options":"nosniff"});
    res.end(content);
  }catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
const candidates=[process.env.AGENTLEDGER_CHROME_PATH,
  "/usr/bin/google-chrome","/usr/bin/chromium","/usr/bin/chromium-browser"].filter(Boolean);
const browserExecutable=candidates.find(existsSync);
let browser;
try{
  if(!browserExecutable)throw Error("Real Chrome required; browser acceptance cannot be skipped");
  browser=await chromium.launch({headless:true,executablePath:browserExecutable,
    args:["--no-sandbox","--disable-gpu"]});
  const page=await browser.newPage({viewport:{width:1280,height:860},
    acceptDownloads:true,reducedMotion:"reduce"});
  const errors=[],failedRequests=[],responses=[];
  page.on("pageerror",e=>errors.push(e.message));
  page.on("requestfailed",r=>failedRequests.push(r.url()+" "+r.failure()?.errorText));
  page.on("response",r=>{if(r.status()>=400)responses.push(r.status()+" "+r.url())});
  const base="http://127.0.0.1:"+server.address().port;
  await page.goto(base+"/",{waitUntil:"networkidle"});
  await page.screenshot({path:join(out,"pocket-startup-diagnostic.png"),fullPage:true});
  const body=(await page.locator("body").innerText()).slice(0,1200);
  console.log("BROWSER STARTUP",JSON.stringify({url:page.url(),body,errors,failedRequests,responses}));
  await page.getByText("进入模拟体验").waitFor({timeout:8000});
  await page.screenshot({path:join(out,"pocket-welcome-desktop.png"),fullPage:true});
  await page.getByText("进入模拟体验").click();
  await page.getByText("我的任务").waitFor();
  await page.getByText("登记支付声明").click();
  await page.getByText("PaymentUnknown").first().waitFor();
  await page.getByText("证据哈希已上链").click();
  await page.getByText("Proof",{exact:true}).click();
  await page.getByText("重算模拟 Evidence SHA-256").click();
  await page.getByText(/本地 SHA-256 已匹配/).waitFor({timeout:10000});
  await page.screenshot({path:join(out,"pocket-proof-desktop.png"),fullPage:true});
  await page.getByText("Journal",{exact:true}).click();
  const promise=page.waitForEvent("download");
  await page.getByText("下载 summary.json").click();
  const dl=await promise;
  if(dl.suggestedFilename()!=="summary.json")throw Error("summary filename mismatch");
  const raw=await readFile(await dl.path(),"utf8");
  const summary=JSON.parse(raw);
  if(summary.source!=="LOCAL_SIMULATION" ||
     summary.paymentStatus!=="PaymentUnknown" ||
     summary.evidenceState!=="DIGEST_MATCHED_ONLY" ||
     summary.chargeAgain!==false ||
     summary.publicTestnetAcceptance!=="BLOCKED_NO_REVIEWED_DEPLOYMENT" ||
     summary.rpcContacted!==false){
    throw Error("downloaded summary violated Phase 0 safety contract");
  }
  await page.getByText("Tasks",{exact:true}).click();
  await page.getByText("丢失支付 ACK").click();
  await page.getByText("PaymentUnknown").first().waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:join(out,"pocket-tasks-mobile.png"),fullPage:true});
  const widths=await page.evaluate(()=>({
    scroll:document.documentElement.scrollWidth,
    visible:document.documentElement.clientWidth,
  }));
  if(widths.scroll>widths.visible+2)throw Error("mobile layout overflow");
  if(errors.length)throw Error("Browser runtime errors: "+errors.join("; "));
  console.log("POCKET CHROMIUM PASS: actual Expo bundle / SHA256 / summary / PaymentUnknown / mobile");
}finally{
  if(browser)await browser.close();
  await new Promise(resolve=>server.close(resolve));
}
