// SPDX-License-Identifier: Apache-2.0
/**
 * Explicit reviewed deployment provenance boundary for LIVE Testnet claims.
 * The allowlist is committed and PR-reviewed, not supplied by the task payer.
 * No deployed contract has been approved merely by making its event match.
 */
import { readFileSync } from "node:fs";
import { keccak256 } from "viem";

const HASH=/^0x[0-9a-fA-F]{64}$/;
const ADDRESS=/^0x[0-9a-fA-F]{40}$/;
const assert=(ok,reason)=>{if(!ok)throw Error(reason)};
const same=(a,b)=>typeof a==="string"&&typeof b==="string"&&a.toLowerCase()===b.toLowerCase();
const defaultRegistry=new URL("./reviewed-testnet-deployments.json",import.meta.url);

export function loadReviewedDeployments() {
  return JSON.parse(readFileSync(defaultRegistry,"utf8"));
}
export function validateReviewedRegistry(registry) {
  assert(registry && typeof registry==="object" &&
    registry.schemaVersion==="agentledger.reviewed-deployments.v1" &&
    registry.network==="eip155:10143" &&
    Array.isArray(registry.deployments),"invalid Testnet reviewed deployment registry");
  const known=new Set();
  for(const deployment of registry.deployments){
    assert(ADDRESS.test(deployment.address||""),"invalid reviewed deployment address");
    assert(ADDRESS.test(deployment.paymentRecorder||""),"invalid reviewed payment Recorder");
    assert(HASH.test(deployment.runtimeKeccak||""),"missing reviewed runtime code hash");
    assert(HASH.test(deployment.deploymentTransaction||""),"missing reviewed deployment transaction");
    assert(typeof deployment.reviewReference==="string" &&
      /^https:\/\/github\.com\/avaargsh\/agent-control-plane\/pull\/\d+$/.test(deployment.reviewReference),
      "reviewed deployment must have explicit repository PR approval reference");
    const id=deployment.address.toLowerCase();
    assert(!known.has(id),"duplicate reviewed contract address");
    known.add(id);
  }
  return registry.deployments;
}
export async function verifyReviewedDeployment(client,ledger,recorder,registry=loadReviewedDeployments()){
  assert(ADDRESS.test(ledger||""),"invalid Ledger address");
  assert(ADDRESS.test(recorder||""),"invalid Recorder address");
  const entries=validateReviewedRegistry(registry);
  const entry=entries.find(item=>same(item.address,ledger));
  assert(entry,"no PR-reviewed Monad Testnet Ledger deployment; live acceptance forbidden");
  assert(same(entry.paymentRecorder,recorder),"trusted Recorder differs from reviewed deployment");
  const runtime=await client.getBytecode({address:ledger});
  assert(typeof runtime==="string" && /^0x[0-9a-fA-F]+$/.test(runtime) &&
    runtime!=="0x","deployed Ledger runtime unavailable");
  const runtimeKeccak=keccak256(runtime).toLowerCase();
  assert(runtimeKeccak===entry.runtimeKeccak.toLowerCase(),
    "on-chain runtime code hash differs from PR-reviewed deployment");
  const txHash=entry.deploymentTransaction.toLowerCase();
  const tx=await client.getTransaction({hash:txHash});
  assert(tx && same(tx.hash,txHash),"reviewed deployment transaction missing or wrong");
  assert(tx.to===null || tx.to===undefined,"reviewed deployment is not a contract creation transaction");
  const receipt=await client.getTransactionReceipt({hash:txHash});
  assert(receipt?.status==="success" && same(receipt.transactionHash,txHash) &&
    same(receipt.contractAddress,ledger),"reviewed deployment receipt does not create Ledger address");
  assert(typeof receipt.blockNumber==="bigint" && HASH.test(receipt.blockHash||""),
    "reviewed deployment receipt lacks block witness");
  const canonical=await client.getBlock({blockNumber:receipt.blockNumber});
  assert(canonical && canonical.number===receipt.blockNumber &&
    same(canonical.hash,receipt.blockHash),
    "reviewed Ledger deployment block is no longer canonical");
  return {
    schemaVersion:"agentledger.reviewed-deployment-witness.v1",
    address:ledger.toLowerCase(),
    recorder:recorder.toLowerCase(),
    deploymentTransaction:txHash,
    blockNumber:receipt.blockNumber.toString(),
    blockHash:receipt.blockHash.toLowerCase(),
    runtimeKeccak,
    reviewReference:entry.reviewReference,
    sourceReviewIsExternal:true,
    finalityProven:false,
  };
}
