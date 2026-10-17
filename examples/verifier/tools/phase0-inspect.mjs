#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// No wallet, payment, retry, ledger write or ACP task mutation.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { inspectPhase0 } from "../lib/phase0-inspect.mjs";

async function main(args) {
  if (args.length !== 1) throw Error("usage: node tools/phase0-inspect.mjs REQUEST_JSON");
  const request = JSON.parse(readFileSync(args[0],"utf8"));
  const result = await inspectPhase0(request);
  console.log(JSON.stringify(result,null,2));
  // PaymentUnknown is NOT a failed charge. The nonzero exit code signifies
  // "not ready to claim both facts", never permission to retry payment.
  if (result.state==="Conflict") process.exitCode=2;
  else if(result.state==="PaymentUnknown") process.exitCode=3;
  else if(result.state!=="EvidenceAnchored") process.exitCode=4;
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch(error=>{
    console.error("PHASE0 INSPECTION NOT VERIFIED: "+error.message);
    process.exitCode=3;
  });
}
