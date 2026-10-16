// SPDX-License-Identifier: Apache-2.0
import { verifyEvidence } from "./evidence-browser.mjs";
import { SCENARIOS, buildDemoCase } from "./demo-scenarios.mjs";

const byId = id => document.getElementById(id);
const HEX40=/^0x[0-9a-fA-F]{40}$/;
const HEX64=/^0x[0-9a-fA-F]{64}$/;
let currentMode="demo";
let currentDemo=null;
let loadedTask=null;
let lookupSequence=0;
function text(id,value) { byId(id).textContent=String(value??"—"); }
function message(id,value,tone="") {
  const el=byId(id);el.textContent=value;el.className="response"+(tone?" "+tone:"");
}
function displayTask(task,kind="SIMULATION") {
  loadedTask=task;
  const live=kind==="TESTNET";
  text("task-network",live?"Monad Testnet · 10143":"Local simulation · NOT CHAIN");
  text("task-value",task.taskId);
  text("task-creator",task.creator);
  text("task-payer",task.payer);
  text("task-amount",task.amountAtomic);
  text("task-budget",task.maxBudgetAtomic);
  text("task-settlement",task.settlementRef);
  text("task-hash",task.evidenceHash);
  text("chain-state",task.status);
  text("chain-flag",live?"ON-CHAIN STATE":"SIMULATED");
  text("ledger-badge",task.status.toUpperCase());
  byId("ledger-badge").className="state-pill "+(task.status==="Proven"?"state-proven":task.status==="Paid"?"state-paid":"");
  text("observation-title",task.status==="Proven"?"Digest anchored":task.status==="Paid"?"Recorder-attested payment":"Created task");
  text("payment-fact","PaymentUnknown");
  text("payment-detail",task.status==="Created"?
    "No verified payer payment attempt. Do not infer permission to spend.":
    "Recorder state alone does not verify USDC Transfer + EIP-3009 nonce.");
  text("evidence-fact",task.status==="Proven"?"Hash anchored · bytes unchecked":"Not anchored");
  text("evidence-detail",task.status==="Proven"?
    "Recompute canonical Evidence SHA-256 against this anchor.":
    "No independently verifiable Evidence anchor available.");
  const explorer=byId("contract-link");
  explorer.hidden=!live;
  if(live)explorer.href="https://testnet.monadscan.com/address/"+encodeURIComponent(task.contract);
  byId("verify-button").disabled=task.status!=="Proven";
  byId("run-sample-proof").disabled=kind!=="SIMULATION"||task.status!=="Proven";
  message("verification-message",task.status==="Proven"?
    (live?"Choose original Evidence files to compare with chain hash.":
      "You can verify this simulated anchor using the generated fixture."):
    "A Proven task is required before Evidence verification.");
}
function clearTask() {
  loadedTask=null;
  byId("verify-button").disabled=true;
  byId("run-sample-proof").disabled=true;
  text("observation-title","No task selected");
  text("ledger-badge","UNKNOWN");
  text("chain-state","Not loaded");
  text("payment-fact","PaymentUnknown");
  text("evidence-fact","Not verified");
  for(const id of ["task-value","task-creator","task-payer","task-amount","task-budget","task-settlement","task-hash"])text(id,"—");
  byId("contract-link").hidden=true;
  message("verification-message","Load a Proven task to verify Evidence.");
}
function switchMode(mode) {
  lookupSequence++; // invalidate any previously in-flight Testnet response
  currentMode=mode;
  byId("demo-controls").hidden=mode!=="demo";
  byId("live-controls").hidden=mode!=="live";
  byId("mode-demo").classList.toggle("active",mode==="demo");
  byId("mode-live").classList.toggle("active",mode==="live");
  byId("mode-demo").setAttribute("aria-pressed",String(mode==="demo"));
  byId("mode-live").setAttribute("aria-pressed",String(mode==="live"));
  const source=byId("source-note");
  source.replaceChildren();
  const strong=document.createElement("strong");
  strong.textContent=mode==="demo"?"LOCAL SIMULATION":"PUBLIC TESTNET STATE ONLY";
  const desc=document.createElement("span");
  desc.textContent=mode==="demo"?
    "Illustrative data generated in this browser — not a USDC settlement or public-chain transaction.":
    "Read-only chain query. Paid / Proven are Recorder and hash-anchor claims, NOT independently verified settlement.";
  source.append(strong,desc);
  clearTask();
  if(mode==="demo")void showScenario("created");
}
async function showScenario(key) {
  if(currentMode!=="demo")return;
  const sequence=++lookupSequence;
  const data=await buildDemoCase(key);
  if(currentMode!=="demo"||sequence!==lookupSequence)return;
  currentDemo=data;
  for(const el of document.querySelectorAll("[data-scenario]")){
    const active=el.dataset.scenario===key;
    el.classList.toggle("active",active);
    el.setAttribute("aria-pressed",String(active));
  }
  text("scenario-heading",data.stage.heading);
  text("scenario-summary",data.stage.summary);
  displayTask(data.task,"SIMULATION");
  text("evidence-fact",data.evidenceFact);
  text("evidence-detail",data.task.status==="Proven"?
    "SIMULATION ONLY · You may reproduce the local digest.":"No independently checked input/output");
}
byId("show-demo").addEventListener("click",()=>{
  switchMode("demo");byId("proof-journey").scrollIntoView({behavior:"smooth"});
});
byId("show-live").addEventListener("click",()=>{
  switchMode("live");byId("live-controls").scrollIntoView({behavior:"smooth",block:"center"});
  byId("contract").focus({preventScroll:true});
});
byId("mode-demo").addEventListener("click",()=>switchMode("demo"));
byId("mode-live").addEventListener("click",()=>switchMode("live"));
for(const button of document.querySelectorAll("[data-scenario]")) {
  button.addEventListener("click",()=>void showScenario(button.dataset.scenario));
}
byId("lookup-form").addEventListener("submit",async event=>{
  event.preventDefault();
  clearTask();
  const number=++lookupSequence;
  const contract=byId("contract").value.trim(),taskId=byId("taskId").value.trim();
  if(!HEX40.test(contract)||!HEX64.test(taskId)){
    message("query-message","Enter a valid contract address and bytes32 task ID.","error");return;
  }
  const btn=byId("query-button");btn.disabled=true;
  message("query-message","Querying Monad Testnet RPC …");
  try{
    const response=await fetch("/api/task?"+new URLSearchParams({contract,taskId}),{
      cache:"no-store"
    });
    const obj=await response.json();
    if(number!==lookupSequence||currentMode!=="live")return;
    if(!response.ok)throw Error(obj.error||"Task lookup unavailable");
    if(obj.chainId!==10143 || obj.taskId?.toLowerCase()!==taskId.toLowerCase() ||
      obj.contract?.toLowerCase()!==contract.toLowerCase()) {
      throw Error("RPC task identity mismatch; no conclusion can be drawn");
    }
    displayTask(obj,"TESTNET");
    message("query-message","Chain state loaded. Payment proof remains unverified.","success");
  }catch(error){
    if(number===lookupSequence&&currentMode==="live"){
      clearTask();
      message("query-message","Lookup unavailable: "+error.message,"error");
    }
  }finally{btn.disabled=false;}
});
for(const id of ["contract","taskId"])byId(id).addEventListener("input",()=>{
  lookupSequence++;
  if(currentMode==="live")clearTask();
});
async function chosenFile(id,maxBytes) {
  const file=byId(id).files?.[0];
  if(!file)return null;
  if(file.size>maxBytes)throw Error("File exceeds size limit: "+file.name);
  return file.arrayBuffer();
}
async function checkEvidence(evidence,input,output,mode) {
  if(!loadedTask ||loadedTask.status!=="Proven")throw Error("No Proven task");
  const result=await verifyEvidence(evidence,loadedTask,input,output);
  if(!result.match)throw Error("Digest mismatch");
  text("evidence-fact",mode==="demo"?"SIMULATED · Hash matched":"Hash matched to chain anchor");
  text("evidence-detail",mode==="demo"?
    "Local-only simulated anchor, not a Monad transaction.":
    "Canonical Evidence bytes match on-chain hash; payment remains unknown.");
  message("verification-message",
    (mode==="demo"?"SIMULATED MATCH":"HASH MATCH")+": "+result.digest+
    ". Original input "+(result.inputChecked?"verified":"not supplied")+
    "; output "+(result.outputChecked?"verified":"not supplied")+
    ". No payment or execution correctness claim.","success");
}
byId("run-sample-proof").addEventListener("click",async()=>{
  if(currentMode!=="demo"||!currentDemo||currentDemo.task.status!=="Proven")return;
  const button=byId("run-sample-proof");button.disabled=true;
  message("verification-message","Hashing local synthetic input, output and Evidence …");
  try{await checkEvidence(currentDemo.evidence,currentDemo.inputBytes,currentDemo.outputBytes,"demo");}
  catch(error){message("verification-message","Verification failed: "+error.message,"error");}
  finally{button.disabled=false;}
});
byId("verify-button").addEventListener("click",async()=>{
  if(!loadedTask||loadedTask.status!=="Proven")return;
  const button=byId("verify-button");button.disabled=true;
  message("verification-message","Computing SHA-256 entirely in this browser …");
  try{
    const evidenceBytes=await chosenFile("evidence-file",2*1024*1024);
    if(!evidenceBytes)throw Error("Select an Evidence JSON file");
    const evidence=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(evidenceBytes));
    const input=await chosenFile("input-file",10*1024*1024);
    const output=await chosenFile("output-file",10*1024*1024);
    await checkEvidence(evidence,input,output,currentMode);
  }catch(error){message("verification-message","NOT VERIFIED: "+error.message,"error");}
  finally{button.disabled=!loadedTask||loadedTask.status!=="Proven";}
});
void showScenario("created");
