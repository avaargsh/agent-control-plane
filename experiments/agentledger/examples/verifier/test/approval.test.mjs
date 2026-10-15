// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { buildApprovalData, verifyTaskPaymentApproval, bindApprovedSettlement } from "../lib/payment-approval.mjs";

const ID = "0x" + "12".repeat(32);
const HASH = "0x" + "23".repeat(32);
const REF = "0x" + "34".repeat(32);
const LEDGER = "0x" + "45".repeat(20);
const PAYEE = "0x" + "67".repeat(20);
const TOKEN = "0x534b2f3A21130d7a60830c2Df862319e593943A3";
const NOW = 1800000000n;

async function signedApproval(overrides = {}) {
  // Ephemeral EOA generated per test; no secret stored in the repository.
  const signer = privateKeyToAccount(generatePrivateKey());
  const quote = {
    schemaVersion: "agentledger.task-payment.v1", chainId: 10143,
    ledger: LEDGER, taskId: ID, descriptionHash: HASH,
    token: TOKEN, payTo: PAYEE, payer: signer.address,
    amountAtomic: "1000", expiresAt: (NOW + 3600n).toString(),
    nonce: "0x" + "ab".repeat(32), ...overrides
  };
  const typed = buildApprovalData(quote);
  const signature = await signer.signTypedData({
    domain: typed.domain, types: typed.types,
    primaryType: typed.primaryType, message: typed.message
  });
  return { ...quote, signature };
}
const chainTask = { status:"Created", taskId:ID, descriptionHash:HASH, maxBudgetAtomic:"2000" };
const settled = payer => ({
  network:"eip155:10143", taskId:ID, token:TOKEN, payer, payTo:PAYEE,
  amountAtomic:"1000", settlementRef:REF
});

test("ephemeral EIP-712 signature verifies for a specific task", async () => {
  const approval = await signedApproval();
  const result = await verifyTaskPaymentApproval(approval, chainTask, {
    nowSeconds: NOW, expectedPayTo: PAYEE
  });
  assert.equal(result.verified, true);
  assert.equal(result.message.amountAtomic, 1000n);
  const proposal = bindApprovedSettlement(approval, settled(approval.payer), chainTask, PAYEE);
  assert.equal(proposal.contract, LEDGER);
  assert.deepEqual(proposal.arguments,[ID,approval.payer.toLowerCase(),"1000",REF]);
});

test("payer signature is invalid after any quote tampering", async () => {
  const approval = await signedApproval();
  await assert.rejects(() => verifyTaskPaymentApproval({...approval, amountAtomic:"999"}, chainTask, {
    nowSeconds:NOW,expectedPayTo:PAYEE
  }),/signature/);
  await assert.rejects(() => verifyTaskPaymentApproval({...approval, payTo:"0x"+"88".repeat(20)}, chainTask, {
    nowSeconds:NOW,expectedPayTo: "0x"+"88".repeat(20)
  }),/signature/);
  await assert.rejects(() => verifyTaskPaymentApproval({...approval, ledger:"0x"+"99".repeat(20)}, chainTask, {
    nowSeconds:NOW,expectedPayTo:PAYEE
  }),/signature/);
});

test("expired, excessive and mismatched task approvals fail closed", async () => {
  const expired = await signedApproval({expiresAt:(NOW-1n).toString()});
  await assert.rejects(() => verifyTaskPaymentApproval(expired, chainTask, {nowSeconds:NOW}),/expired/);
  const long = await signedApproval({expiresAt:(NOW+86401n).toString()});
  await assert.rejects(() => verifyTaskPaymentApproval(long, chainTask, {nowSeconds:NOW}),/24-hour/);
  const expensive = await signedApproval({amountAtomic:"3000"});
  await assert.rejects(() => verifyTaskPaymentApproval(expensive, chainTask, {nowSeconds:NOW}),/budget/);
  const correct = await signedApproval();
  await assert.rejects(() => verifyTaskPaymentApproval(correct,{...chainTask,status:"Paid"},{nowSeconds:NOW}),/Created/);
  await assert.rejects(() => verifyTaskPaymentApproval(correct,{...chainTask,taskId:REF},{nowSeconds:NOW}),/task ID/);
  await assert.rejects(() => verifyTaskPaymentApproval(correct,{...chainTask,descriptionHash:REF},{nowSeconds:NOW}),/description/);
});

test("cross task / payee / payer / amount settlement substitution fails", async () => {
  const approval = await signedApproval();
  const receipt = settled(approval.payer);
  assert.throws(()=>bindApprovedSettlement(approval,{...receipt,taskId:REF},chainTask,PAYEE),/task mismatch/);
  assert.throws(()=>bindApprovedSettlement(approval,{...receipt,payer:"0x"+"ee".repeat(20)},chainTask,PAYEE),/payer mismatch/);
  assert.throws(()=>bindApprovedSettlement(approval,{...receipt,payTo:"0x"+"ee".repeat(20)},chainTask,PAYEE),/payee mismatch/);
  assert.throws(()=>bindApprovedSettlement(approval,{...receipt,amountAtomic:"999"},chainTask,PAYEE),/amount mismatch/);
  assert.throws(()=>bindApprovedSettlement(approval,receipt,chainTask,"0x"+"dd".repeat(20)),/operator payee/);
  assert.throws(()=>bindApprovedSettlement(approval,receipt,{...chainTask,status:"Proven"},PAYEE),/Created/);
});

test("non-USDC, wrong chain and malformed approval are rejected", () => {
  const good = {schemaVersion:"agentledger.task-payment.v1",chainId:10143,ledger:LEDGER,taskId:ID,descriptionHash:HASH,
    token:TOKEN,payTo:PAYEE,payer:PAYEE,amountAtomic:"1000",expiresAt:"1800003600",nonce:REF};
  assert.throws(()=>buildApprovalData({...good,chainId:1}),/network/);
  assert.throws(()=>buildApprovalData({...good,token:LEDGER}),/USDC/);
  assert.throws(()=>buildApprovalData({...good,amountAtomic:"-1"}),/amountAtomic/);
});
