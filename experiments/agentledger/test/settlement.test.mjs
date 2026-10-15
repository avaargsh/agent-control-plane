// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { NETWORK, USDC_CONTRACT, TRANSFER_TOPIC, ERC3009_EVENT0, verifyReceipt, verifyLive, validatePaymentResponse } from '../tools/settlement.mjs';

const transaction = '0x' + 'c1'.repeat(32);
const taskId = '0x' + '01'.repeat(32);
const blockHash = '0x' + 'b1'.repeat(32);
const payer = '0x' + '22'.repeat(20);
const payTo = '0x' + '33'.repeat(20);
const topic = addr => '0x' + '0'.repeat(24) + addr.slice(2);
const log = {
  address:USDC_CONTRACT, topics:[TRANSFER_TOPIC, topic(payer), topic(payTo)],
  data:'0x'+'0'.repeat(61)+'3e8', transactionHash:transaction, blockHash,
  logIndex:'0x5', removed:false
};
const receipt = { transactionHash:transaction, blockHash, blockNumber:'0x64', status:'0x1', logs:[log] };
const expected = { taskId, transaction, payer, payTo, amountAtomic:'1000', network:NETWORK };
const verified = () => verifyReceipt(receipt, expected, '0x65');

test('matching USDC transfer returns deterministic settlement reference', () => {
  const result = verified();
  assert.equal(result.amountAtomic, '1000');
  assert.equal(result.logIndex, 5);
  assert.match(result.settlementRef, /^0x[0-9a-f]{64}$/);
  assert.equal(result.settlementRef, verified().settlementRef);
});
test('payer/recipient/token/amount mismatch fail closed', () => {
  for (const change of [
    { payer:'0x'+'44'.repeat(20) }, { payTo:'0x'+'55'.repeat(20) },
    { amountAtomic:'1001' }, { token:'0x'+'66'.repeat(20) }, { network:'eip155:143' },
  ]) assert.throws(() => verifyReceipt(receipt, {...expected,...change},'0x65'));
});
test('missing receipt, revert, reorg and confirmations fail closed', () => {
  assert.throws(() => verifyReceipt(null, expected, '0x65'));
  assert.throws(() => verifyReceipt({...receipt, status:'0x0'}, expected, '0x65'));
  assert.throws(() => verifyReceipt(receipt,expected,'0x64',{minConfirmations:2}));
  assert.throws(() => verifyReceipt({...receipt,logs:[{...log, removed:true}]},expected,'0x65'));
  assert.throws(() => verifyReceipt({...receipt, logs:[log,log]},expected,'0x65'));
  assert.throws(() => verifyReceipt({...receipt,blockHash:'0x'+'ee'.repeat(32)},expected,'0x65'));
});

test('EIP-3009 settlement requires the actual USDC AuthorizationUsed nonce in the same receipt', () => {
  const nonce = '0x' + 'ad'.repeat(32);
  const authLog = {
    address: USDC_CONTRACT,
    topics: [ERC3009_EVENT0, topic(payer), nonce],
    data: '0x', transactionHash: transaction, blockHash, logIndex: '0x6', removed: false,
  };
  const expectedNonce = { ...expected, paymentNonce: nonce };
  assert.throws(() => verifyReceipt(receipt, expectedNonce, '0x65'), /AuthorizationUsed/);
  const proven = verifyReceipt({ ...receipt, logs: [log, authLog] }, expectedNonce, '0x65');
  assert.equal(proven.paymentNonce, nonce);
  assert.equal(proven.amountAtomic, '1000');
  for (const changed of [
    { ...authLog, topics: [ERC3009_EVENT0, topic(payer), '0x' + 'bb'.repeat(32)] },
    { ...authLog, topics: [ERC3009_EVENT0, topic(payTo), nonce] },
    { ...authLog, address: payer },
    { ...authLog, blockHash: '0x' + 'ff'.repeat(32) },
  ]) {
    assert.throws(() => verifyReceipt({ ...receipt, logs: [log, changed] }, expectedNonce, '0x65'), /AuthorizationUsed/);
  }
  assert.throws(() => verifyReceipt({ ...receipt, logs: [log, authLog, authLog] }, expectedNonce, '0x65'), /AuthorizationUsed/);
  assert.throws(() => verifyReceipt({ ...receipt, logs: [log, authLog] },
    { ...expectedNonce, paymentNonce: null }, '0x65'), /paymentNonce/);
});

test('x402 response must match chain, payer and transfer', () => {
  const response = { success:true, network:NETWORK, transaction, payer, amount:'1000' };
  assert.doesNotThrow(() => validatePaymentResponse(response,expected));
  assert.throws(() => validatePaymentResponse({...response,success:false},expected));
  assert.throws(() => validatePaymentResponse({...response,payer:payTo},expected));
  assert.throws(() => validatePaymentResponse({...response,transaction:'0x'+'11'.repeat(32)},expected));
  assert.throws(() => validatePaymentResponse({...response, amount:'999'},expected));
});
test('RPC verifier checks chain id and returns validated receipt, no network required',async () => {
  const calls=[];
  const stub=async (_url,init)=>{
    const {method}=JSON.parse(init.body);calls.push(method);
    const result={eth_chainId:'0x279f',eth_getTransactionReceipt:receipt,eth_blockNumber:'0x65'}[method];
    return { ok:true,json:async()=>({jsonrpc:'2.0',id:1,result}) };
  };
  const result=await verifyLive({...expected,paymentResponse:{success:true,network:NETWORK,transaction,payer}},'http://127.0.0.1:8545',stub);
  assert.equal(result.amountAtomic,'1000');
  assert.deepEqual(calls,['eth_chainId','eth_getTransactionReceipt','eth_blockNumber']);
  await assert.rejects(()=>verifyLive(expected,'https://example.com',async()=>({ok:true,json:async()=>({result:'0x8f'})})));
});
