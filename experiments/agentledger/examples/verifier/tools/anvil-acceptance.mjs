#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
/**
 * Hermetic local Anvil acceptance.
 *
 * IMPORTANT: A trusted recorder SIMULATES a payment attestation; this DOES
 * NOT transfer USDC and must NEVER be called a real x402/Monad payment test.
 * Uses ephemeral EOA keys and anvil_setBalance on loopback ONLY. Does not
 * accept wallet keys, mainnet RPCs, or broadcast to a public testnet.
 */
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  createPublicClient, createWalletClient, http, parseEther, toHex, parseEventLogs,
} from "viem";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { runDemoTask } from "./run-demo-task.mjs";
import { readTask } from "../lib/task-reader.mjs";
import { createVerifierServer } from "../server.mjs";
import { verifyEvidence } from "../public/evidence-browser.mjs";
import { projectPhase0 } from "../../../phase0/status.mjs";

const hash = bytes => "0x" + createHash("sha256").update(bytes).digest("hex");
const rpcUrl = process.env.AGENTLEDGER_ANVIL_RPC || "http://127.0.0.1:18545";
const url = new URL(rpcUrl);
assert.equal(url.protocol, "http:", "Anvil acceptance requires loopback HTTP, not public HTTPS");
assert.ok(["127.0.0.1", "localhost"].includes(url.hostname), "Anvil only; nonlocal RPC refused");
const chain = {
  id: 10143,
  name: "Anvil Local Monad-Like Test EVM (NOT Monad Testnet)",
  nativeCurrency: {name:"Anvil Ether",symbol:"ETH",decimals:18},
  rpcUrls: {default:{http:[rpcUrl]}},
};
const publicClient = createPublicClient({chain,transport:http(rpcUrl,{timeout:10000,retryCount:0})});

function wallet(account) {
  return createWalletClient({account,chain,transport:http(rpcUrl,{timeout:10000,retryCount:0})});
}
async function mined(tx, label) {
  const receipt = await publicClient.waitForTransactionReceipt({hash:tx,timeout:20_000});
  assert.equal(receipt.status,"success",label+" reverted");
  return receipt;
}
async function main() {
  assert.equal(await publicClient.getChainId(), 10143, "Anvil chain ID mismatch");
  // Check the existence of Anvil-only cheat method BEFORE generating keys
  // or attempting any transaction. Even a real Monad Testnet using chain ID
  // 10143 cannot pass this local RPC and cheat-method requirement.
  const creator = privateKeyToAccount(generatePrivateKey());
  const recorder = privateKeyToAccount(generatePrivateKey());
  const payer = privateKeyToAccount(generatePrivateKey());
  for (const account of [creator,recorder]) {
    await publicClient.request({
      method:"anvil_setBalance",
      params:[account.address,toHex(parseEther("5"))]
    });
    assert.ok(await publicClient.getBalance({address:account.address}) > 0n);
  }
  const contractArtifact = JSON.parse(readFileSync(
    new URL("../../../out/AgentLedger.sol/AgentLedger.json",import.meta.url),
    "utf8"
  ));
  const bytecode = contractArtifact.bytecode?.object;
  assert.ok(typeof bytecode === "string" && /^0x[0-9a-fA-F]+$/.test(bytecode), "Forge bytecode missing");

  const deployment = await wallet(creator).deployContract({
    abi:contractArtifact.abi,bytecode,args:[recorder.address],gas:6_000_000n
  });
  const deployed = await mined(deployment,"deploy");
  const contract = deployed.contractAddress;
  assert.ok(/^0x[0-9a-fA-F]{40}$/.test(contract));

  const temp = mkdtempSync(join(tmpdir(),"agentledger-anvil-"));
  try {
    const inputPath=join(temp,"input.txt");
    const outputPath=join(temp,"output.json");
    const evidencePath=join(temp,"evidence.json");
    const input=Buffer.from("Monad local EVM acceptance: one deterministic tool call.\n","utf8");
    writeFileSync(inputPath,input);
    const taskId=hash(randomBytes(32));
    const descriptionHash=hash(input);
    const settlementRef=hash(Buffer.concat([randomBytes(16),Buffer.from("SIMULATED-ONLY")]));

    const taskTx=await wallet(creator).writeContract({
      address:contract,abi:contractArtifact.abi,functionName:"createTask",
      args:[taskId,descriptionHash,1000n],gas:400_000n
    });
    await mined(taskTx,"createTask");
    let task=await readTask(publicClient,contract,taskId);
    assert.equal(task.status,"Created");
    assert.equal(task.descriptionHash,descriptionHash);
    assert.equal(projectPhase0({task,attempt:"NotStarted"}).state,"Created");
    // Payment has NOT happened; this must refuse to run.
    await assert.rejects(
      ()=>runDemoTask({contract,taskId,inputPath,outputPath,evidencePath},{chain:publicClient}),
      /not Paid/
    );

    // LOCAL ANVIL DEMO ONLY: do not claim this is a verified USDC settlement.
    const payTx=await wallet(recorder).writeContract({
      address:contract,abi:contractArtifact.abi,functionName:"recordPayment",
      args:[taskId,payer.address,1000n,settlementRef],gas:400_000n
    });
    const simulatedPaymentReceipt=await mined(payTx,"recordPayment SIMULATED");
    const attestedLogs=parseEventLogs({
      abi:contractArtifact.abi,logs:simulatedPaymentReceipt.logs,
      eventName:"PaymentAttested",strict:true
    });
    assert.equal(attestedLogs.length,1,"expected simulated PaymentAttested event");
    assert.equal(attestedLogs[0].args.settlementRef,settlementRef);
    task=await readTask(publicClient,contract,taskId);
    assert.equal(task.status,"Paid");
    assert.equal(task.amountAtomic,"1000");
    assert.equal(task.settlementRef,settlementRef);
    const noUsdcPaymentProof=projectPhase0({task,attempt:"Reserved"});
    assert.equal(noUsdcPaymentProof.state,"PaymentUnknown");
    assert.equal(noUsdcPaymentProof.payment.recorderAttestationObserved,true);
    assert.equal(noUsdcPaymentProof.payment.eventIndependentlyChecked,false);

    const executed=await runDemoTask(
      {contract,taskId,inputPath,outputPath,evidencePath},{chain:publicClient}
    );
    assert.equal(executed.status,"EXECUTED_OFFCHAIN_NOT_ANCHORED");
    await assert.rejects(
      ()=>runDemoTask({contract,taskId,inputPath,outputPath,evidencePath},{chain:publicClient}),
      /already exists/
    );

    const proofTx=await wallet(creator).writeContract({
      address:contract,abi:contractArtifact.abi,functionName:"submitEvidence",
      args:[taskId,executed.evidenceHash],gas:400_000n
    });
    const anchorReceipt=await mined(proofTx,"submitEvidence");
    const anchorLogs=parseEventLogs({
      abi:contractArtifact.abi,logs:anchorReceipt.logs,
      eventName:"EvidenceAnchored",strict:true
    });
    assert.equal(anchorLogs.length,1,"expected independently visible EvidenceAnchored event");
    assert.equal(anchorLogs[0].args.evidenceHash,executed.evidenceHash);
    task=await readTask(publicClient,contract,taskId);
    assert.equal(task.status,"Proven");
    assert.equal(task.evidenceHash,executed.evidenceHash);
    const evidence=JSON.parse(readFileSync(evidencePath,"utf8"));
    assert.equal((await verifyEvidence(evidence,task,
      readFileSync(inputPath),readFileSync(outputPath))).match,true);
    const separatedFacts=projectPhase0({
      task,attempt:"Reserved",evidence:"HashMatched"
    });
    assert.equal(separatedFacts.state,"PaymentUnknown");
    assert.equal(separatedFacts.payment.eventIndependentlyChecked,false);
    assert.equal(separatedFacts.evidence.bytesIndependentlyChecked,true);
    await assert.rejects(
      ()=>verifyEvidence({...evidence,executor:"forged"},task),/SHA-256 mismatch/
    );

    // The same read-only HTTP endpoint consumed by the demonstration UI
    // must independently reproduce Proven and the exact evidence digest.
    const server=createVerifierServer({
      lookup:(address,id)=>readTask(publicClient,address,id)
    });
    await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
    try {
      const base="http://127.0.0.1:"+server.address().port;
      const response=await fetch(base+"/api/task?"+new URLSearchParams({contract,taskId}));
      assert.equal(response.status,200);
      const loaded=await response.json();
      assert.equal(loaded.status,"Proven");
      assert.equal(loaded.evidenceHash,executed.evidenceHash);
    } finally {
      await new Promise(resolve=>server.close(resolve));
    }

    // Persist a small, non-sensitive acceptance manifest for CI artifacts.
    const manifest={
      schemaVersion:"agentledger.anvil-acceptance.v1",
      network:"ANVIL_LOCAL_SIMULATION_NOT_MONAD_TESTNET",
      chainId:10143,
      paymentMode:"SIMULATED_RECORDER_ATTESTATION_NO_USDC_TRANSFER",
      contract,taskId,
      txHashes:{deploy:deployment,createTask:taskTx,simulatedRecordPayment:payTx,submitEvidence:proofTx},
      statuses:["Created","Paid","Proven"],
      evidenceHash:executed.evidenceHash,
      phase0:{
        schemaVersion:separatedFacts.schemaVersion,
        externallySafeState:separatedFacts.state,
        paymentFact:separatedFacts.payment.fact,
        recorderAttestationEventObserved:attestedLogs.length===1,
        usdcTransferAndNonceVerified:false,
        evidenceAnchorEventObserved:anchorLogs.length===1,
        evidenceBytesMatched:separatedFacts.evidence.bytesIndependentlyChecked,
        executionAuthority:"ACP",
        safeToRecharge:false
      },
      independentReadback:{abi:true,browserApi:true,localEvidenceSha256:true},
      result:"PASS_LOCAL_EVM_ONLY",
    };
    const target=fileURLToPath(new URL("../../../out/agentledger-anvil-acceptance.json",import.meta.url));
    writeFileSync(target,JSON.stringify(manifest,null,2)+"\n");
    console.log(JSON.stringify(manifest,null,2));
  } finally {
    rmSync(temp,{recursive:true,force:true});
  }
}
if (process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  main().catch(err=>{
    console.error("ANVIL LOCAL ACCEPTANCE FAILED: "+err.message);
    process.exitCode=1;
  });
}
