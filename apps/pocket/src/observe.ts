// SPDX-License-Identifier: Apache-2.0
// Narrow, task-only HTTP adapter. No JSON-RPC client, wallet, custom transport,
// arbitrary URL parameter or write method is exposed by the app.
const ADDRESS=/^0x[0-9a-fA-F]{40}$/;
const HASH=/^0x[0-9a-fA-F]{64}$/;
export type TaskRead={
  chainId:10143;contract:string;taskId:string;
  creator:string;payer:string;
  status:"Created"|"Paid"|"Proven";
  amountAtomic:string;maxBudgetAtomic:string;
  descriptionHash:string;evidenceHash:string;settlementRef:string;
};
function isUint(value:unknown):value is string{
 return typeof value==="string"&&/^(0|[1-9][0-9]*)$/.test(value);
}
export function validateTaskIdentity(value:unknown,contract:string,taskId:string):TaskRead{
 const v=(value&&typeof value==="object"?value:{} ) as Record<string,unknown>;
 if(v.chainId!==10143||typeof v.contract!=="string"||
    v.contract.toLowerCase()!==contract.toLowerCase()||
    typeof v.taskId!=="string"||v.taskId.toLowerCase()!==taskId.toLowerCase())
    throw Error("CHAIN_TASK_IDENTITY_MISMATCH");
 if(!["Created","Paid","Proven"].includes(String(v.status)))throw Error("INVALID_CHAIN_TASK_STATUS");
 for(const name of ["creator","payer"])if(typeof v[name]!=="string"||!ADDRESS.test(v[name]))throw Error("INVALID_CHAIN_ACTOR");
 for(const name of ["descriptionHash","evidenceHash","settlementRef"])
   if(typeof v[name]!=="string"||!HASH.test(v[name]))throw Error("INVALID_CHAIN_HASH");
 for(const name of ["amountAtomic","maxBudgetAtomic"])
   if(!isUint(v[name]))throw Error("INVALID_CHAIN_AMOUNT");
 return v as TaskRead;
}
export function resolveVerifierUrl(raw:string){
 const url=new URL(raw);
 if(url.username||url.password||url.search||url.hash||
   (url.pathname!=="/"&&url.pathname!==""))
    throw Error("VERIFIER_BASE_URL_INVALID");
 const local=url.protocol==="http:"&&["localhost","127.0.0.1","[::1]"].includes(url.hostname);
 if(url.protocol!=="https:"&&!local)throw Error("VERIFIER_REQUIRES_HTTPS_OR_LOOPBACK");
 return url.origin;
}
export async function observeTaskReadOnly(
  contract:string,taskId:string,
  baseUrl:string="http://127.0.0.1:4321",
):Promise<{task:TaskRead;paymentStatus:"PaymentUnknown";chargeAgain:false;source:"TESTNET_TASK_ONLY";publicTestnetAcceptance:"BLOCKED_NO_REVIEWED_DEPLOYMENT"}>{
 if(!ADDRESS.test(contract)||!HASH.test(taskId))throw Error("INVALID_TASK_LOOKUP");
 const base=resolveVerifierUrl(baseUrl);
 const endpoint=base+"/api/task?"+new URLSearchParams({contract,taskId});
 // The verifier service itself pins the RPC chain ID and exposes GET only.
 // It is NOT the approved-deployment or full paid-acceptance verifier.
 const abort=new AbortController();
 const timeout=setTimeout(()=>abort.abort(),10000);
 try{
  const res=await fetch(endpoint,{
   method:"GET",credentials:"omit",cache:"no-store",
   redirect:"error",signal:abort.signal,
  });
  // This is an untrusted task-only observer, not a generic HTTP transport.
  // Reject redirect chains, non-JSON responses and oversized bodies before
  // interpreting any returned task as a contract-origin observation.
  if(res.redirected)throw Error("TASK_READ_REDIRECT_FORBIDDEN");
  if(!res.ok)throw Error("TASK_READ_UNAVAILABLE_"+res.status);
  const type=res.headers.get("content-type")||"";
  const mediaType=type.split(";")[0].trim().toLowerCase();
  if(mediaType!=="application/json" &&
     !(mediaType.startsWith("application/") && mediaType.endsWith("+json")))
   throw Error("TASK_READ_CONTENT_TYPE_INVALID");
  const maxBytes=64*1024;
  const size=Number(res.headers.get("content-length")||"0");
  if(!Number.isFinite(size)||size>maxBytes)throw Error("TASK_READ_RESPONSE_TOO_LARGE");
  const raw=await res.text();
  if(raw.length>maxBytes)throw Error("TASK_READ_RESPONSE_TOO_LARGE");
  const task=validateTaskIdentity(JSON.parse(raw),contract,taskId);
  return Object.freeze({
   task,paymentStatus:"PaymentUnknown",chargeAgain:false,
   source:"TESTNET_TASK_ONLY",
   publicTestnetAcceptance:"BLOCKED_NO_REVIEWED_DEPLOYMENT",
  });
 } finally{clearTimeout(timeout);}
}

export function taskReadSummary(task:TaskRead,verification="NOT_VERIFIED"){
 return {
  schemaVersion:"agentledger.pocket.summary.v1",
  mode:"TESTNET_TASK_READ_ONLY",outcome:"TASK_OBSERVED_NOT_PAYMENT_ACCEPTANCE",
  task:{taskId:task.taskId,descriptionHash:task.descriptionHash,ledgerState:task.status},
  chainId:task.chainId,contract:task.contract,
  rpc:{identity:"local read-only verifier / pinned Monad RPC",contacted:true},
  payment:{observed:"PaymentUnknown",recorderClaim:task.status==="Created"?"NotRecorded":"Paid",
   transferEvent:"NOT_INDEPENDENTLY_VERIFIED",
   authorizationUsed:"NOT_INDEPENDENTLY_VERIFIED",nonce:null,chargeAgain:false},
  evidence:{evidenceHash:task.evidenceHash,verification,
   executionCorrectnessVerified:false},
  security:{reviewedDeployment:false,
   publicTestnetAcceptance:"BLOCKED_NO_REVIEWED_DEPLOYMENT",
   mainnetAccepted:false,realFundsUsed:false,executionAuthority:"ACP"},
 };
}
