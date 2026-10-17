#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Public onsite/hackathon preflight: no signers, no transaction broadcast.
// Default mode reads ONLY local repo state; --network issues bounded
// read-only eth_chainId and facilitator GET /supported requests.
import {readFileSync,writeFileSync,existsSync} from "node:fs";
import {fileURLToPath,pathToFileURL} from "node:url";
import {resolve,dirname} from "node:path";

export const MONAD_TESTNET=Object.freeze({
 chainId:10143,network:"eip155:10143",rpc:"https://testnet-rpc.monad.xyz/",
 usdc:"0x534b2f3A21130d7a60830c2Df862319e593943A3",
 facilitator:"https://x402-facilitator.molandak.org",
 docs:"https://docs.monad.xyz/guides/x402",
 networkDocs:"https://docs.monad.xyz/developer-essentials/testnet",
});
const root=fileURLToPath(new URL("../",import.meta.url));
const at=relative=>resolve(root,relative);
const required=Object.freeze([
 "apps/pocket/App.tsx",
 "apps/pocket/package.json",
 "apps/pocket/src/evidence.ts",
 "apps/pocket/src/observe.ts",
 "examples/verifier/server.mjs",
 "examples/verifier/tools/demo-journey.mjs",
 "examples/verifier/tools/live-acceptance.mjs",
 "sdk/package.json",
 "docs/application-map.md",
 "phase0/reviewed-testnet-deployments.json",
]);
function noSecrets(text){
 return typeof text==="string" && !/(PRIVATE_KEY|MNEMONIC|TOKEN|SIGNATURE|PASSWORD|AUTHORIZATION)/i.test(text);
}
export function localPreflight({hasFile=existsSync,read=readFileSync}={}){
 const missing=required.filter(p=>!hasFile(at(p)));
 let registry=null,registryState="UNREADABLE",registrations=0;
 if(!missing.includes("phase0/reviewed-testnet-deployments.json")){
  try{
   registry=JSON.parse(read(at("phase0/reviewed-testnet-deployments.json"),"utf8"));
   if(registry.schemaVersion!=="agentledger.reviewed-deployments.v1"||
      registry.network!=="eip155:10143"||!Array.isArray(registry.deployments))
     throw Error("registry schema mismatch");
   registrations=registry.deployments.length;
   registryState=registrations===0?"NO_REVIEWED_DEPLOYMENTS":"ENTRIES_REQUIRE_INDEPENDENT_REVIEW";
  }catch{registryState="INVALID_REVIEWED_DEPLOYMENT_REGISTRY";}
 }
 return {
  schemaVersion:"agentledger.onsite-preflight.v1",
  mode:"LOCAL_ONLY_NO_NETWORK",
  product:"AgentLedger Pocket",
  entrypoints:{
   pocket:"experiments/agentledger/apps/pocket",
   verifier:"experiments/agentledger/examples/verifier",
   sdk:"experiments/agentledger/sdk",
   extractedCompatibility:"experiments/agentledger-sdk (not a separate app)"
  },
  files:{present:required.length-missing.length,expected:required.length,missing,ready:missing.length===0},
  testnet:{...MONAD_TESTNET,networkContacted:false},
  deployment:{registryState,entries:registrations,
   publicPaidAcceptance:"BLOCKED_UNLESS_INDEPENDENTLY_REVIEWED"},
  payment:{state:"PaymentUnknown",independentlyVerified:false,chargeAgain:false,
   nonceReused:false,actualUSDCSpend:false},
  evidence:{simulatedHashCheckAvailable:!missing.includes("apps/pocket/src/evidence.ts"),
   noExecutionCorrectnessAttested:true},
  safeToDemo:missing.length===0,
  safeToClaimPublicTestnetAcceptance:false,
  requiredBeforeTestnetAcceptance:[
   "Reviewed bytecode and contract deployment, authorized Recorder",
   "A separate, consciously authorized Testnet-only transaction",
   "Independent USDC Transfer and AuthorizationUsed nonce + EIP-712 payer intent",
   "Precharge reservation, Recorder event and Evidence anchor",
   "Canonical block reread; distinguish observation from finality",
  ],
 };
}
async function readOnlyJson(url,fetchImpl){
 const response=await fetchImpl(url,{method:"GET",redirect:"error",signal:AbortSignal.timeout(8000)});
 if(!response.ok)throw Error("HTTP_"+response.status);
 return response.json();
}
export async function networkPreflight({fetchImpl=fetch}={}){
 const rpc=new URL(MONAD_TESTNET.rpc);
 const facilitator=new URL(MONAD_TESTNET.facilitator);
 if(rpc.protocol!=="https:"||facilitator.protocol!=="https:")
  throw Error("HTTPS_NETWORK_ENDPOINT_REQUIRED");
 const result={
  contacted:true,
  rpc:{endpoint:rpc.origin,method:"eth_chainId",status:"UNAVAILABLE",chainId:null},
  facilitator:{endpoint:facilitator.origin+"/supported",status:"UNAVAILABLE",
   advertisedMonadExact:false},
 };
 try{
  const response=await fetchImpl(rpc.href,{
   method:"POST",headers:{"content-type":"application/json"},
   body:JSON.stringify({jsonrpc:"2.0",id:1,method:"eth_chainId",params:[]}),
   redirect:"error",signal:AbortSignal.timeout(8000),
  });
  if(!response.ok)throw Error("HTTP_"+response.status);
  const data=await response.json();
  if(data.result!=="0x279f")throw Error("WRONG_CHAIN_ID");
  result.rpc.status="MATCHED_TESTNET_10143";
  result.rpc.chainId=10143;
 }catch(error){result.rpc.status=error.message==="WRONG_CHAIN_ID"?"WRONG_CHAIN_ID":"UNAVAILABLE";}
 try{
  const data=await readOnlyJson(new URL("/supported",facilitator).href,fetchImpl);
  const kinds=Array.isArray(data.kinds)?data.kinds:[];
  result.facilitator.advertisedMonadExact=kinds.some(k=>k.network==="eip155:10143" &&
   (k.scheme==="exact"||k.scheme==="v2-eip155-exact"));
  result.facilitator.status=result.facilitator.advertisedMonadExact?
   "NETWORK_SCHEME_ADVERTISED_NOT_SETTLEMENT_PROOF":"NOT_ADVERTISED";
 }catch{result.facilitator.status="UNAVAILABLE";}
 return result;
}
export async function runPreflight({network=false,fetchImpl=fetch}={}){
 const report=localPreflight();
 if(network) {
  report.mode="READ_ONLY_PUBLIC_ENDPOINTS";
  report.testnet.networkContacted=true;
  report.network=await networkPreflight({fetchImpl});
 }
 return report;
}
export async function main(args){
 if(args.includes("--help")){
  process.stdout.write("node tools/onsite-preflight.mjs [--network] [--output PATH]\\n"+
   "No wallet, signer, payment or chain writes. Without --network no endpoints contacted.\\n");
  return 0;
 }
 const rest=args.filter(x=>x!=="--network");
 const index=rest.indexOf("--output");
 let output=null;
 if(index!==-1){
  output=rest[index+1];
  rest.splice(index,2);
 }
 if(rest.length||index!==-1&&(!output||output.startsWith("--")))
  throw Error("USAGE_ERROR_USE_--help");
 const report=await runPreflight({network:args.includes("--network")});
 const json=JSON.stringify(report,null,2)+"\n";
 if(output){
  if(!noSecrets(output))throw Error("OUTPUT_PATH_MUST_NOT_CONTAIN_SECRETS");
  // Preserve any existing field acceptance evidence.
  writeFileSync(output,json,{flag:"wx",mode:0o600});
 } else process.stdout.write(json);
 if(!report.safeToDemo) return 2;
 if(args.includes("--network")&&
   (report.network.rpc.status!=="MATCHED_TESTNET_10143"||
    report.network.facilitator.advertisedMonadExact!==true))return 2;
 return 0;
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href){
 main(process.argv.slice(2)).then(code=>{process.exitCode=code}).catch(error=>{
  console.error("FIELD_PREFLIGHT_FAILED: "+(
   ["USAGE_ERROR_USE_--help","OUTPUT_PATH_MUST_NOT_CONTAIN_SECRETS"].includes(error.message)?
    error.message:"CHECK_INPUTS_AND_OUTPUT_PATH"
  ));
  process.exitCode=2;
 });
}
