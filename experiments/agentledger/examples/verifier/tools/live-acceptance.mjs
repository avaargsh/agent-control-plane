#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Read-only public Monad Testnet acceptance. No signer, wallet or broadcast.
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { runLiveAcceptance } from "../lib/live-acceptance.mjs";
import { loadReviewedDeployments, validateReviewedRegistry } from "../lib/reviewed-deployment.mjs";

async function main(args) {
  if(args.length!==2) {
    throw Error("usage: node tools/live-acceptance.mjs REQUEST_JSON REPORT_JSON");
  }
  const [inputPath,outputPath]=args;
  if(inputPath===outputPath) throw Error("input and report paths must differ");
  const request=JSON.parse(readFileSync(inputPath,"utf8"));
  // Preflight before *any* public RPC call or wallet-related processing:
  // no reviewed deploy means no public Testnet acceptance can be claimed.
  const approved=validateReviewedRegistry(loadReviewedDeployments());
  if(!approved.some(item=>item.address.toLowerCase()===request.ledger?.toLowerCase())) {
    throw Error("PUBLIC_TESTNET_ACCEPTANCE_BLOCKED_NO_REVIEWED_DEPLOYMENT");
  }
  const report=await runLiveAcceptance(request);
  // Never overwrite prior acceptance evidence: a new observation requires a
  // distinct artifact and a separately recorded review.
  writeFileSync(outputPath,JSON.stringify(report,null,2)+"\n",{flag:"wx",mode:0o600});
  console.log("LIVE TESTNET OBSERVATION SAVED (NOT FINALITY): "+outputPath);
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch(error=>{
    // Never print payer signatures, authorization bodies, wallet keys, or
    // untrusted JSON as diagnostic output.
    if(error.message==="PUBLIC_TESTNET_ACCEPTANCE_BLOCKED_NO_REVIEWED_DEPLOYMENT"){
      console.error("PUBLIC_TESTNET_ACCEPTANCE_BLOCKED_NO_REVIEWED_DEPLOYMENT: no reviewed deployment in repository allowlist; local Anvil/simulated journey still available. No recharge.");
    }else{
      console.error("NO LIVE ACCEPTANCE: read-only evidence incomplete or inconsistent. No recharge.");
    }
    process.exitCode=3;
  });
}
