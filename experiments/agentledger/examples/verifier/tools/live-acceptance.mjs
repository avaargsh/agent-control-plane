#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Read-only public Monad Testnet acceptance. No signer, wallet or broadcast.
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { runLiveAcceptance } from "../lib/live-acceptance.mjs";

async function main(args) {
  if(args.length!==2) {
    throw Error("usage: node tools/live-acceptance.mjs REQUEST_JSON REPORT_JSON");
  }
  const [inputPath,outputPath]=args;
  if(inputPath===outputPath) throw Error("input and report paths must differ");
  const request=JSON.parse(readFileSync(inputPath,"utf8"));
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
    console.error("NO LIVE ACCEPTANCE: read-only evidence incomplete or inconsistent. No recharge.");
    process.exitCode=3;
  });
}
