// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { buildApprovalData, USDC_CONTRACT } from "../../../lib/approval-format.mjs";
import {
  AMOUNT_ATOMIC, APPROVAL_HEADER, PAYMENT_HEADER, TRANSFER_TYPES,
  authorizeHttpRequest, guardConfig, parseHeader, reservePaymentAttempt,
  verifyTaskQuote, verifyX402Payload,
} from "../lib/challenge-guard.mjs";

const NOW=1800000000n;
const LEDGER="0x"+"12".repeat(20);
const PAYEE="0x"+"23".repeat(20);
const TASK="0x"+"34".repeat(32);
const DESCRIPTION="0x"+"45".repeat(32);
const QUOTE_NONCE="0x"+"56".repeat(32);
const PAY_NONCE="0x"+"67".repeat(32);
const config=guardConfig({ledger:LEDGER,payTo:PAYEE,taskId:TASK,publicOrigin:"http://127.0.0.1:4021"});
const chainTask={taskId:TASK,descriptionHash:DESCRIPTION,status:"Created",maxBudgetAtomic:AMOUNT_ATOMIC};
const b64=(o)=>Buffer.from(JSON.stringify(o),"utf8").toString("base64");

async function signedFixture() {
  const account=privateKeyToAccount(generatePrivateKey());
  const quote={
    schemaVersion:"agentledger.task-payment.v1",chainId:10143,ledger:LEDGER,
    taskId:TASK,descriptionHash:DESCRIPTION,token:USDC_CONTRACT,payTo:PAYEE,
    payer:account.address,amountAtomic:AMOUNT_ATOMIC,
    expiresAt:String(NOW+3600n),nonce:QUOTE_NONCE,
  };
  const typed=buildApprovalData(quote);
  quote.signature=await account.signTypedData({
    domain:typed.domain,types:typed.types,primaryType:typed.primaryType,message:typed.message,
  });
  const auth={
    from:account.address,to:PAYEE,value:AMOUNT_ATOMIC,
    validAfter:String(NOW-5n),validBefore:String(NOW+90n),nonce:PAY_NONCE,
  };
  const paySignature=await account.signTypedData({
    domain:{name:"USDC",version:"2",chainId:10143,verifyingContract:USDC_CONTRACT},
    types:TRANSFER_TYPES,primaryType:"TransferWithAuthorization",
    message:{
      from:account.address,to:PAYEE,value:BigInt(auth.value),
      validAfter:BigInt(auth.validAfter),validBefore:BigInt(auth.validBefore),nonce:auth.nonce,
    }
  });
  const payment={
    x402Version:2,
    resource:{url:config.resourceURL},
    accepted:{scheme:"exact",network:"eip155:10143",amount:AMOUNT_ATOMIC,asset:USDC_CONTRACT,payTo:PAYEE},
    payload:{authorization:auth,signature:paySignature},
  };
  return {quote,payment};
}
test("quote and EIP-3009 signed transfer match same task, payer, recipient and amount",async()=>{
  const {quote,payment}=await signedFixture();
  const approved=await verifyTaskQuote(quote,config,chainTask,NOW);
  const result=await verifyX402Payload(payment,approved,config,NOW);
  assert.equal(result.nonce,PAY_NONCE);
});
test("task quote tampering or chain task mismatch fails before settlement",async()=>{
  const {quote}=await signedFixture();
  for(const [changed,error] of [
    [{...quote,amountAtomic:"1001"},/quote amount/],
    [{...quote,ledger:"0x"+"ff".repeat(20)},/ledger mismatch/],
    [{...quote,payTo:"0x"+"ff".repeat(20)},/payee mismatch/],
    [{...quote,expiresAt:String(NOW-1n)},/expired/],
    [{...quote,nonce:"0x"+"aa".repeat(32)},/signature invalid/],
  ]){
    await assert.rejects(()=>verifyTaskQuote(changed,config,chainTask,NOW),error);
  }
  await assert.rejects(()=>verifyTaskQuote(quote,config,{...chainTask,status:"Paid"},NOW),/not Created/);
  await assert.rejects(()=>verifyTaskQuote(quote,config,{...chainTask,maxBudgetAtomic:"999"},NOW),/budget/);
});
test("malformed or manipulated payment envelope fails without settlement",async()=>{
  const {quote,payment}=await signedFixture();
  const approved=await verifyTaskQuote(quote,config,chainTask,NOW);
  const changes=[
    {...payment,resource:{url:"https://different.example/tasks/"+TASK+"/run"}},
    {...payment,accepted:{...payment.accepted,amount:"1001"}},
    {...payment,accepted:{...payment.accepted,payTo:"0x"+"ee".repeat(20)}},
    {...payment,accepted:{...payment.accepted,network:"eip155:1"}},
    {...payment,accepted:{...payment.accepted,extra:{assetTransferMethod:"permit2"}}},
    {...payment,accepted:{...payment.accepted,extra:{assetTransferMethod:"erc7710"}}},
    {...payment,accepted:{...payment.accepted,extra:{name:"Unexpected Token",version:"2"}}},
    {...payment,payload:{...payment.payload,authorization:{...payment.payload.authorization,from:"0x"+"bb".repeat(20)}}},
    {...payment,payload:{...payment.payload,authorization:{...payment.payload.authorization,nonce:"0x"+"aa".repeat(32)}}},
    {...payment,payload:{...payment.payload,authorization:{...payment.payload.authorization,validBefore:String(NOW+3700n)}}},
    {...payment,payload:{...payment.payload,signature:"0x"+"00".repeat(65)}},
  ];
  for(const change of changes) await assert.rejects(()=>verifyX402Payload(change,approved,config,NOW));
});
test("unsigned request permits 402 only after valid task quote; bad signatures do not reserve",async()=>{
  const {quote,payment}=await signedFixture();
  const headers={[APPROVAL_HEADER]:b64(quote)};
  let reservations=0;
  const deps={
    nowSeconds:NOW,
    readTask:async()=>chainTask,
    reserve:()=>{reservations++;return {result:"RESERVED_FIRST_ATTEMPT"}},
  };
  const initial=await authorizeHttpRequest(headers,config,deps);
  assert.equal(initial.phase,"CHALLENGE_ALLOWED");
  assert.equal(reservations,0);
  const retried=await authorizeHttpRequest({...headers,[PAYMENT_HEADER]:b64(payment)},config,deps);
  assert.equal(retried.phase,"ONE_PAYMENT_ATTEMPT_RESERVED");
  assert.equal(reservations,1);
  await assert.rejects(()=>authorizeHttpRequest({
    ...headers,[PAYMENT_HEADER]:b64({...payment,resource:{url:"https://evil.example/"}})
  },config,deps));
  assert.equal(reservations,1);
});
test("base64 decoding rejects malformed, noncanonical and oversized headers",()=>{
  assert.throws(()=>parseHeader("abc;def","bad"),/base64/);
  assert.throws(()=>parseHeader("a".repeat(8193),"bad"),/too large/);
  assert.throws(()=>parseHeader(b64([]),"bad"),/object/);
  assert.throws(()=>guardConfig({...config,publicOrigin:"https://example.com/unsafe/path"}),/path/);
});
test("precharge reservation allows one paid authorization; same or altered retry is blocked",async t=>{
  const dir=mkdtempSync(join(tmpdir(),"agentledger-precharge-"));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const {quote,payment}=await signedFixture();
  const headers={[APPROVAL_HEADER]:b64(quote),[PAYMENT_HEADER]:b64(payment)};
  const deps={
    nowSeconds:NOW,readTask:async()=>chainTask,
    reserve:payload=>reservePaymentAttempt(payload,{dbPath:join(dir,"charge.db")}),
  };
  const first=await authorizeHttpRequest(headers,config,deps);
  assert.equal(first.reservation.result,"RESERVED_FIRST_ATTEMPT");
  await assert.rejects(()=>authorizeHttpRequest(headers,config,deps),/reservation blocked/);
  // Even a distinct valid payment authorization for the same task is refused.
  const digest="0x"+createHash("sha256").update("different").digest("hex");
  assert.throws(()=>reservePaymentAttempt({
    schemaVersion:"agentledger.precharge.v1",ledger:LEDGER,taskId:TASK,payer:quote.payer.toLowerCase(),
    approvalNonce:QUOTE_NONCE,paymentNonce:"0x"+"99".repeat(32),
    quoteDigest:digest,payloadDigest:digest
  },{dbPath:join(dir,"charge.db")}),/reservation blocked/);
});
