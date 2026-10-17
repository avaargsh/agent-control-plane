# AgentLedger: x402 payment reconciliation (Monad Testnet only)

## Scope

The x402 gate is now **task-scoped**: set `TASK_ID` to a 32-byte ID already
created on the AgentLedger contract. The only billable route is
`GET /tasks/<TASK_ID>/run`, with a fixed $0.001 testnet USDC price.
The response is **demo data**, not a real Agent execution.

After x402 successful settlement, the resource server's
`onAfterSettle` hook attempts an exclusive (`wx`) local write to
`receipts/<txHash>.json`. This is a **pending journal notice**, NOT a
validated payment or a chain event. Crash between settlement and journaling
is still possible. The payer should retain the original x402
`PAYMENT-RESPONSE` header; reconciliation can be recovered from its
`network`, `transaction`, and `payer` fields.

## Run

Use a dedicated testnet wallet and the public Monad / Circle faucets.
Never configure production wallets in this demo.

```bash
cd experiments/agentledger
node --test test/*.test.mjs
forge test -vvv
node tools/facilitator-preflight.mjs

cd examples/x402-gate
npm install
export TASK_ID=0xYOUR_EXISTING_BYTES32_TASK_ID
export PAY_TO_ADDRESS=0xYOUR_TESTNET_PAYEE
npm start
curl -i "http://127.0.0.1:4021/tasks/$TASK_ID/run"
# Expect an HTTP 402. A compatible x402 v2 client can authorize USDC payment.
```

After a genuine x402 client pays, inspect the locally saved
`experiments/agentledger/receipts/<txHash>.json`.
The file includes: task ID, payment payer, merchant recipient,
chain/network, expected amount (1000 USDC atomic units), transaction hash,
and optional x402 settlement response.

```bash
cd experiments/agentledger
node tools/settlement.mjs verify receipts/0xYOUR_TRANSACTION_HASH.json
```

The read-only verifier:

1. Gets `eth_chainId` and rejects anything other than Monad Testnet (10143).
2. Gets `eth_getTransactionReceipt`, rejects missing/reverted receipts.
3. Requires at least two observed blocks (configurable by programmatic caller).
4. Matches **one** ERC-20 Transfer from the official Monad Testnet USDC
   contract, with exact sender, receiver and value.
5. Checks x402 settlement response against transaction, network and payer.
6. If an EIP-3009 `paymentNonce` is included, **requires exactly one**
   `AuthorizationUsed(payer, paymentNonce)` event from the official USDC
   contract in **the same receipt/block/transaction**, in addition to
   the matching Transfer. Missing, duplicate, tampered or unrelated nonce
   events fail closed. This proves the token consumed that authorization,
   not that the Agent delivered its service.
7. Generates a domain-separated SHA-256 `settlementRef` from
   network, token, transaction and log index.

The resulting `settlementRef` is a candidate for the contract's
`recordPayment(taskId, payer, amountAtomic, settlementRef)` and uniquely
identifies the transfer in `usedSettlementRefs`.

## Important non-goals / security boundary

**DO NOT automate `recordPayment` just because `settlement.mjs`
reports a matching Transfer.** The transfer receipt alone **does not**
cryptographically bind that transfer to the task, the approved quote,
the authorized request or the payer's intended purchase. The current
recorded `taskId` is server context, not on-chain payment intent.

Before promoting a payment to Paid, the recorder must also durably prove:

- A preexisting task quote bound to taskId, descriptionHash, the expected
  network, token, payee, amount, expiry and authorized payer.
- Independent payer approval of this particular quote/task;
  never infer such approval solely from ERC-20 Transfer events.
- Atomic claim/idempotency of the unique settlement reference and a
  reconciliation path after timeout, lost response or RPC reorg.
- Approval of the trusted recorder to attest this exact task.

Neither N confirmations nor a successful receipt alone proves irreversible
finality. Finality criteria must be chosen explicitly with Monad consensus
semantics and RPC provider behavior.

The contract's `maxBudget` is a cap on the amount attested by the
trusted recorder; it does NOT constrain offchain wallet spending.

The x402 server intentionally does not hold signing keys, broadcast
`recordPayment`, modify an external execution system, or mark tasks Proven.
Any recovered file can be verified independently with the script, but
the path from approved quote → receipt → authorized ledger mutation →
actual Agent execution still requires work.

## Acceptance cases (current)

- matching USDC token, transaction, payer, payee and exact 1000 atomic units;
- mismatched token, chain, payer, payee or amount;
- missing receipt, reverted transaction or insufficient block depth;
- removed/reorged Transfer log, wrong block hash or duplicate matching logs;
- successful/failing/mismatched x402 response;
- RPC chain-ID mismatch and hermetic RPC mocks.

Run `node --test test/settlement.test.mjs` locally. No real USDC transfer
or onchain deployment is claimed by these tests.


## Pre-charge → consumed nonce → post-charge admission

For the `examples/x402-gate` path, the post-settle notice now includes
the EIP-3009 `paymentNonce`. The read-only receipt verifier demands the
matching EIP-3009 `AuthorizationUsed` event; an unrelated transfer of the
same amount cannot be accepted as that paid authorization.

`examples/verifier/tools/prepare-record.mjs` additionally requires a
durable matching pre-charge record in
`receipts/charge-attempts.db` (task, EIP-712 quote digest, signer,
quote nonce and consumed payment nonce) before it creates a post-settlement
`PREPARED` recorder candidate. A failed check leaves the post-settlement
admission journal untouched. No recorder wallet signing occurs.

A matching pair of events in a successful receipt remains weaker than
chain finality and cannot prove model execution, delivery, refund rights,
or correct linkage to a serialized HTTP 402 challenge. The pre-charge
reservation is local-only; live testnet acceptance and recovery still need
a dedicated test wallet and independent evidence.
