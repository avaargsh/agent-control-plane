# Signed task quote → x402 authorization pre-charge gate

**Status:** a fail-closed Monad **Testnet-only** HTTP admission experiment. It is
NOT proof of production-safe billing, irreversible finality or completed
Agent execution.

## Threat addressed

Previously, a signed EIP-712 quote was only inspected **after** a USDC
transaction had already been settled. A task ID in an HTTP URL did not
prevent a payment from being charged to a wrong or unrelated task.

The x402 gate now runs an authorization middleware **before**
`paymentMiddleware`:

1. Load the payer's EIP-712 `TaskPaymentApproval` from a bounded
   Base64 `X-AgentLedger-Approval` header.
2. Query the actual Monad Testnet `getTask(taskId)`, verify deployed
   contract code and exact `Created` state, descriptionHash, budget and
   configured ledger address.
3. Verify the payer's EIP-712 signature, network 10143, task ID,
   description, payee, official Testnet USDC asset, **1000 atomic units**
   (0.001 USDC), expiry and quote nonce.
4. For a request without `PAYMENT-SIGNATURE`, pass to x402 to
   produce its standard HTTP 402 challenge. Invalid quotes are rejected
   **before** challenge issuance.
5. For a paid retry, decode the x402 v2 `PAYMENT-SIGNATURE`, require
   its `resource.url` to match an **exact preconfigured origin and path**,
   and confirm `accepted` network, asset, payee and amount.
6. Independently verify the EIP-3009
   `TransferWithAuthorization(from,to,value,validAfter,validBefore,nonce)`
   signature against the official Testnet USDC verifying contract. Payer,
   amount, payee and expiry must agree with the **signed task approval**.
7. **Before** the request can reach the x402 facilitator,
   atomically insert one `RESERVED_RECONCILE_ONLY` entry into a
   local SQLite pre-charge journal keyed by
   `(ledger,taskId)`, `(ledger,payer,approvalNonce)`,
   `(ledger,quoteDigest)` and EIP-3009 payment nonce.
   Any replay, including **identical retries**, is blocked.

A fail to create the reservation denies settlement. Restarting the
service preserves the reservation in SQLite (WAL, synchronous FULL).
The `onAfterSettle` hook also journals an **unverified** transaction
notice and EIP-3009 nonce for later chain reconciliation.

## Running the demo gate

Run from `examples/x402-gate`:

```bash
npm install --no-audit --no-fund --ignore-scripts
export LEDGER_ADDRESS=0xYOUR_DEPLOYED_MONAD_TESTNET_LEDGER
export TASK_ID=0xYOUR_EXISTING_CREATED_TASK_ID
export PAY_TO_ADDRESS=0xYOUR_TESTNET_USDC_RECEIVER
export PUBLIC_ORIGIN=http://127.0.0.1:4021
export PORT=4021
npm test
npm start
```

Do not use any valuable wallet, mainnet key or production merchant account.

Clients MUST preserve the identical `X-AgentLedger-Approval` header
on the initial and paid-retry HTTP requests. This Base64 value contains
the JSON EIP-712 quote envelope **with signature**, but no private key.
The client must also use an x402 v2 wallet to attach `PAYMENT-SIGNATURE`
on the retry, targeting the exact `PUBLIC_ORIGIN` + resource path.

Example headers (illustrative, not a live signature):

```http
GET /tasks/0xTASKID/run HTTP/1.1
Host: 127.0.0.1:4021
X-AgentLedger-Approval: BASE64_OF_SIGNED_TASK_APPROVAL_JSON
```

Server first validates the quote, then issues an ordinary x402
`PAYMENT-REQUIRED` challenge. The retry MUST keep the task approval
header and attach the valid x402 `PAYMENT-SIGNATURE`. On restart or
network timeout, do not blindly retry the paid request: inspect the
reservation and reconcile with the actual chain transaction.

## Durable reservation semantics

`tools/charge-attempt-journal.py` (Python standard library) owns only
**pre-charge attempt reservations**. It is separate from
`tools/admission-journal.py` (post-settlement recorder candidates).

The pre-charge DB is
`receipts/charge-attempts.db`, or set
`AGENTLEDGER_CHARGE_DB` to a private local path. No private keys or
secrets are stored in it.

Once reserved, the task is intentionally **not re-chargeable** through
this server, even if facilitator verification fails, the process crashes
or the response disappears. The operator must inspect the on-chain
EIP-3009 nonce usage and USDC Transfer event, recover pending transaction
details, and decide on an explicit new quote/compensation procedure.

To inspect a nonce after a timeout (read-only):

```bash
cd .
python3 tools/charge-attempt-journal.py inspect 0xTHE_64_HEX_PAYMENT_NONCE
```

The post-settlement admission tool requires this exact reserved nonce to
be consumed in the same USDC transaction's EIP-3009 `AuthorizationUsed`
event, along with the correct `Transfer` event. Neither event is enough
alone; the EIP-712 payer quote must match the reserved quote digest.

There is deliberately no reset/clear/retry command. Deleting the DB,
running multiple disconnected servers or using a second Facilitator
defeats the local safety boundary.

## Limitations / why this is still a Draft

- A signed EIP-3009 payment may be valid but not yet paid. A local
  `RESERVED_RECONCILE_ONLY` row is **NOT** proof of settlement.
- The x402 resource URL is matched to a configured origin/path, but
  the client does not sign the exact serialized 402 challenge bytes.
  The economic terms and destination are independently cross-checked.
- Failed x402 verification may strand a reserved task. This is an
  **intentional fail-closed trade-off**, not a successful automatic
  recovery flow.
- A single local SQLite instance does not enforce cluster-wide
  idempotency or quota across other facilitators, clients or chains.
- Signature authorization is EOA only; ERC-1271 wallets, proxies, key
  rotation, audit of a real payer session and independent challenge
  attestation are not implemented.
- The post-settlement journal is best-effort. After a crash before its
  write, recovery needs the user's original signed payment data or
  the verified transaction hash.
- No refund, escrow, finality oracle, automatic `recordPayment`,
  automatically executed Agent task or `submitEvidence` broadcast
  is implemented by this gate.
- **Always** check the actual Testnet transaction and task state before
  claiming a complete paid task.

Tests include EIP-712 tampering, wrong task/payer/payee/asset/amount,
wrong network/resource URL/expiry, EIP-3009 signature tampering, invalid
headers, and duplicate/cross-task pre-charge reservations.
