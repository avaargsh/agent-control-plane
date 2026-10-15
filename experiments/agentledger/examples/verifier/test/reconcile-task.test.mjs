// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { hashTypedData } from "viem";
import { buildApprovalData } from "../lib/payment-approval.mjs";
import { reconcileTask } from "../lib/reconcile-task.mjs";

const LEDGER = "0x" + "11".repeat(20);
const TASK = "0x" + "22".repeat(32);
const DESCRIPTION = "0x" + "33".repeat(32);
const PAYEE = "0x" + "44".repeat(20);
const REF = "0x" + "55".repeat(32);
const NONCE = "0x" + "66".repeat(32);
const TX = "0x" + "77".repeat(32);
const ZERO = "0x" + "0".repeat(64);
const USDC_CONTRACT = "0x534b2f3A21130d7a60830c2Df862319e593943A3";

async function setup(status = "Created", overrides = {}) {
  const signer = privateKeyToAccount(generatePrivateKey());
  const quote = {
    schemaVersion:"agentledger.task-payment.v1", chainId:10143, ledger:LEDGER,
    taskId:TASK, descriptionHash:DESCRIPTION, token:USDC_CONTRACT,
    payTo:PAYEE, payer:signer.address, amountAtomic:"1000",
    nonce:"0x"+"aa".repeat(32),
    // Old quote deliberately expired: post-payment reconciliation still works.
    expiresAt:"1700000000",
  };
  const typed = buildApprovalData(quote);
  const signature = await signer.signTypedData({
    domain:typed.domain,types:typed.types,primaryType:typed.primaryType,message:typed.message
  });
  const approval = {...quote, signature};
  const digest = hashTypedData({
    domain:typed.domain,types:typed.types,primaryType:typed.primaryType,message:typed.message
  }).toLowerCase();
  const task = {
    chainId:10143,contract:LEDGER,taskId:TASK,descriptionHash:DESCRIPTION,
    payer:status === "Created" ? "0x"+"0".repeat(40) : signer.address,
    status,maxBudgetAtomic:"1000",
    amountAtomic:status === "Created" ? "0" : "1000",
    settlementRef:status === "Created" ? ZERO : REF,
  };
  const transfer = {
    taskId:TASK,payer:signer.address,payTo:PAYEE,token:USDC_CONTRACT,
    network:"eip155:10143",amountAtomic:"1000",settlementRef:REF,
    paymentNonce:NONCE,transaction:TX,blockNumber:"100",confirmations:"2",
  };
  const precharge = {result:"PRECHARGE_RECORD_FOUND",admission:{
    status:"RESERVED_RECONCILE_ONLY",ledger:LEDGER,
    task_id:TASK,payer:signer.address.toLowerCase(),
    approval_nonce:quote.nonce,quote_digest:digest,payment_nonce:NONCE,
  }};
  let counts = {task:0,receipt:0,reservation:0};
  const deps = {
    chain:{},
    readTask:async()=>{counts.task++;return {...task,...overrides.task};},
    verifySettlement:async()=>{counts.receipt++;return {...transfer,...overrides.transfer};},
    inspectPrecharge:async()=>{counts.reservation++;return {
      ...precharge,admission:{...precharge.admission,...overrides.admission},
    };},
  };
  const args = {approval,pendingReceipt:{paymentNonce:NONCE,transaction:TX},
    expectedPayTo:PAYEE};
  return {args,deps,counts};
}

test("verified payment still Created: manual recorder review, NEVER recharge",async()=>{
  const {args,deps}=await setup("Created");
  const result=await reconcileTask(args,deps);
  assert.equal(result.classification,"SETTLEMENT_OBSERVED_LEDGER_CREATED");
  assert.equal(result.status,"READ_ONLY_NO_RECHARGE");
  assert.match(result.manualAction,/NEVER pay again/);
});
test("recovered Paid/Proven verifies amount, payer and settlement reference",async()=>{
  for(const status of ["Paid","Proven"]){
    const {args,deps}=await setup(status);
    const result=await reconcileTask(args,deps);
    assert.equal(result.classification,status === "Paid" ? "PAID_PAYMENT_MATCH" : "PROVEN_PAYMENT_MATCH");
  }
});
test("tampered signature fails before RPC access",async()=>{
  const {args,deps,counts}=await setup("Created");
  args.approval.amountAtomic="1001";
  await assert.rejects(()=>reconcileTask(args,deps),/signature invalid/);
  assert.equal(counts.task,0);
  assert.equal(counts.receipt,0);
});
test("invalid precharge, nonce or transaction mismatch refuse with no mutation",async()=>{
  for(const overrides of [
    {transfer:{paymentNonce:"0x"+"ff".repeat(32)}},
    {transfer:{amountAtomic:"999"}},
    {transfer:{payer:PAYEE}},
    {admission:{quote_digest:ZERO}},
    {admission:{payment_nonce:ZERO}},
    {task:{descriptionHash:ZERO}},
    {task:{chainId:1}},
    {task:{maxBudgetAtomic:"999"}},
  ]){
    const {args,deps}=await setup("Created",overrides);
    await assert.rejects(()=>reconcileTask(args,deps));
  }
});
test("Paid state with wrong settlementRef never classifies as successfully paid",async()=>{
  const {args,deps}=await setup("Paid",{task:{settlementRef:ZERO}});
  await assert.rejects(()=>reconcileTask(args,deps),/LEDGER_CONFLICT/);
});
test("Proven state with wrong payer does not become proof of payment",async()=>{
  const {args,deps}=await setup("Proven",{task:{payer:PAYEE}});
  await assert.rejects(()=>reconcileTask(args,deps),/LEDGER_CONFLICT/);
});
test("missing transaction nonce fails closed, preventing a blind second payment",async()=>{
  const {args,deps,counts}=await setup("Created");
  delete args.pendingReceipt.paymentNonce;
  await assert.rejects(()=>reconcileTask(args,deps),/missing pending EIP-3009 nonce/);
  assert.equal(counts.receipt,0);
});

test("live acceptance can explicitly bind settlement RPC to trusted task RPC",async()=>{
  const {args,deps}=await setup("Proven");
  const observed=[];
  deps.settlementRpcUrl="https://testnet-rpc.monad.xyz/";
  const original=deps.verifySettlement;
  deps.verifySettlement=async (pending,rpc)=>{
    observed.push(rpc);
    return original(pending,rpc);
  };
  const state=await reconcileTask(args,deps);
  assert.equal(state.classification,"PROVEN_PAYMENT_MATCH");
  assert.deepEqual(observed,["https://testnet-rpc.monad.xyz/"]);
});
