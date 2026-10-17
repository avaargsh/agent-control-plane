# Verification Studio — Phase 0 demo acceptance

**Target:** `examples/verifier/` (local read-only app) and
`sdk/@agentledger/verify@0.2.0-alpha.2` (private source preview).
**Safety:** every walkthrough scenario is LOCAL SIMULATION. No browser
wallet, no charge, no chain write, no execution-control mutation and no real Monad
Testnet payment acceptance.

## 1. Open Studio

```bash
cd experiments/agentledger/examples/verifier
npm install --ignore-scripts --no-audit --no-fund
npm start
# Open http://127.0.0.1:4321/#proof-journey
```

Choose **Simulation**. The yellow source banner says `LOCAL SIMULATION`.
The right-hand observation panel separates Ledger's state from the
independently verified payment state. **Paid** is a Recorder claim and
is **not** an independently verified USDC payment.

## 2. Demonstrate the happy visual path (about 60 seconds)

1. Click **01 Created**. Show `taskId`, `descriptionHash`,
   chain ID 10143, simulated contract address, and **No RPC contacted**.
2. Click **02 Paid · Recorder claim**. The Ledger says **Paid**, but
   **PaymentUnknown** remains because no verified USDC `Transfer`,
   EIP-3009 `AuthorizationUsed` or consumed nonce exists.
3. Click **03 Evidence → Verify**. Show the anchored `evidenceHash`.
4. Click **Verify simulated Evidence**. The browser rehashes canonical
   JSON plus raw input/output, yielding **SIMULATED MATCH**. Payment
   stays **PaymentUnknown**. This proves bytes, not execution correctness.

The simulation shows the pinned Testnet RPC **as an identity label only**;
there is no RPC request to that endpoint. No real transaction hashes or
nonces are invented.

## 3. PaymentUnknown alternative (about 30 seconds)

Click **04 PaymentUnknown / Lost ACK**. The Recorder says Paid, but
the HTTP response might have been lost after a charge attempt. The
page retains `PaymentUnknown` and **no recharge**. An absent receipt
is not proof that the charge failed. Never release the reservation,
reuse the nonce or retry x402 automatically.

## 4. Cross-Ledger and wrong-identity rejection (optional 30 seconds)

```bash
cd experiments/agentledger/examples/verifier
node tools/demo-journey.mjs
```

The standalone, deterministic CLI exercises the exact browser fixture
generator and SHA-256 checker, then calls the Phase 0 projector with:

- a receipt for another Ledger address but the **same** task ID;
- an otherwise matching receipt with the **wrong payer**.

Both must be **REJECTED**, not promoted to `PaymentRecorded` or
`EvidenceAnchored`. It also validates the lost ACK path and outputs:

```text
experiments/agentledger/out/demo-journey/summary.json
```

Run from `examples/verifier` as shown; the default path is resolved
relative to the source script, not the working directory. Or pass an
explicit destination as the first CLI positional argument.

The CI job **Read-only UI and contract query smoke** executes the same
journey **and launches real headless Chromium** against the local Studio
server (no external RPC). Browser automation clicks Created, Paid,
Evidence and Verify; asserts Recorder Paid does not promote
`PaymentUnknown`; downloads and parses `summary.json`; checks the
Lost ACK no-recharge branch, and repeats at mobile viewport.
It uploads `agentledger-studio-simulated-summary` containing
`summary.json`, `studio-desktop.png` and `studio-mobile.png`.
The JSON contains `taskId`, `descriptionHash`, Ledger contract,
chain ID, pinned RPC identifier (with `contacted:false`),
recorder claim, USDC event/nonce uncertainty, `evidenceHash`,
digest match, error-path rejection and an explicit
`BLOCKED_NO_REVIEWED_DEPLOYMENT` public-chain acceptance status.

## 5. Public Testnet acceptance remains BLOCKED

The checked-in `phase0/reviewed-testnet-deployments.json` list is
empty. The `tools/live-acceptance.mjs` CLI fails **before RPC access** with:

```text
PUBLIC_TESTNET_ACCEPTANCE_BLOCKED_NO_REVIEWED_DEPLOYMENT
```

This is a safety gate, not a broken simulated demo. Anvil's local
contract/deployment/attestation/evidence round trip and the simulated
browser/CLI path still work independently.

A real acceptance requires a PR-reviewed contract deployment, actual
Testnet USDC Transfer+AuthorizationUsed nonce, signed task intent and
pre-charge reservation, a matching Recorder transaction, a matching
Evidence anchor and canonical block rechecks. Even then observation
does **not** prove irreversible finality or correct Agent execution.

## 6. Public SDK boundary

```bash
cd experiments/agentledger/sdk
npm install --ignore-scripts --no-audit --no-fund
npm test && npm run packcheck
```

Only `createAgentLedgerVerifier` and two immutable metadata exports
are present at the module root. Factory instances expose:
`observe(contract,taskId)`, `verify(contract,taskId,evidence,...)`
and `reconcile(expectedPaymentReceipt)`. The last one is **receipt-only**
reconciliation; it intentionally returns `PaymentUnknown` and cannot
promote payment to Paid without signed quote and durable reservation
validation by the separate Phase 0 CLI.

No fixture parser, injectable `client`/`transport`, signing, write
or execution-control API is exported. Transport injection is refused at factory
construction **before** nonce or RPC processing. No npm publish is
permitted (`private:true`).

### Optional local Chromium visual regression

```bash
cd experiments/agentledger/examples/verifier
npm install --ignore-scripts --no-audit --no-fund
# Requires locally installed Chrome/Chromium; set AGENTLEDGER_CHROME_PATH if needed.
npm run test:browser
```

This exercise starts its own loopback-only GET verifier service with a
mock that throws if a simulated scenario attempts public RPC, runs
headless Chromium, generates screenshots, downloads the browser-authored
`summary.json`, verifies its required fields, and fails if there are
browser runtime errors or horizontal overflow at 390px viewport.
It is a **real graphical interaction test**, not just a Node model
fixture check. It does not contact a Testnet payment facilitator.
