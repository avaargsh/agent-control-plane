# AgentLedger Phase 0 — Live Monad Testnet **observation-only** acceptance

**This verifier is read-only and non-custodial.** It cannot sign a
transaction, charge a wallet, refund, execute an Agent or advance ACP
Desired State. Its only success result is `PASS_OBSERVED_NOT_FINALITY`.

## Scope and prereqs

Phase 0 is **not** an Agent Runtime. ACP retains run ownership,
authorization, retry/resume, OutcomeContract, and independent execution
proof. Ledger is only a settlement/receipt/evidence adapter.

The following must already have happened through **separate, approved
test-only wallet operations**:

1. On Monad Testnet, deploy AgentLedger and create the task.
2. Approve the EIP-712 task quote and send one x402 v2 payment using a
   dedicated Testnet wallet. Preserve the signed approval and original
   payment response, including `transaction`, `paymentNonce`, payer,
   payee, amount, token and chain. Never repeat a paid request after lost ACK.
3. Verify USDC `Transfer` + EIP-3009 `AuthorizationUsed` through the
   official Testnet RPC. Keep the original local pre-charge SQLite DB.
4. A trusted authorized Recorder **separately** records the already
   reconciled settlement on-chain; preserve the `recordPayment` tx hash.
5. ACP approves and runs a task. After generating the
   `agentledger.evidence.v1` JSON and exact original input/output files,
   an authorized actor **separately** anchors the digest with
   `submitEvidence`; preserve its tx hash.

**None of the above funds, approvals or writes is automated by this
verifier.** The real-wallet payment and finality policy must be reviewed
before any live use.

## Run

Install Node.js >=22. Keep the official RPC URL pinned for an actual
Testnet acceptance claim:

```bash
cd experiments/agentledger/examples/verifier
npm install --no-audit --no-fund --ignore-scripts
export MONAD_RPC_URL=https://testnet-rpc.monad.xyz
export AGENTLEDGER_CHARGE_DB="$(pwd)/../../receipts/charge-attempts.db"
node tools/live-acceptance.mjs ./live-request.json ./live-observation.json
```

The acceptance command rejects all other RPC origins, including Anvil
with chain ID 10143. Ordinary `phase0-inspect.mjs` may still use other
RPC providers for separate verification; the one pinned origin is a
**claim-generation safety restriction**, not a guarantee that a RPC
provider is universally trustworthy.

Example `live-request.json` structure (placeholders are intentionally
invalid, never commit signed quote files or personal task inputs):

```json
{
  "schemaVersion": "agentledger.phase0.live-acceptance.v1",
  "network": "eip155:10143",
  "ledger": "0xYOUR_REAL_TESTNET_LEDGER",
  "taskId": "0xYOUR_REAL_BYTES32_TASK_ID",
  "attempt": "Reserved",
  "payment": {
    "approval": {"REPLACE_WITH_ACTUAL_SIGNED_EIP712_APPROVAL": true},
    "pendingReceipt": {"REPLACE_WITH_ACTUAL_X402_PAYMENT_NOTICE": true},
    "expectedPayTo": "0xYOUR_REAL_TESTNET_USDC_RECEIVER"
  },
  "evidence": {
    "file": "./real-evidence.json",
    "inputFile": "./original-input.txt",
    "outputFile": "./actual-output.json"
  },
  "recorderTransaction": "0xYOUR_REAL_RECORDPAYMENT_TX_HASH",
  "anchorTransaction": "0xYOUR_REAL_SUBMITEVIDENCE_TX_HASH"
}
```

### Independent checks

The existing Phase 0 inspector re-verifies the exact **EIP-712 approval**,
local pre-charge reservation and **USDC payment events** using RPC:
chain ID 10143, correct USDC token, `Transfer`, same-transaction
`AuthorizationUsed`, nonce/payer/payee/amount, receipt status and the
**currently canonical block at the receipt height**. It also re-hashes
Evidence JSON and the original input/output bytes against the on-chain
`Proven` digest. It must return `EvidenceAnchored`.

The acceptance layer additionally:

- Re-reads the on-chain task independently, preventing a stale verified
  projection from being used after a state change.
- Queries the actual `recordPayment` transaction and validates exactly
  one `PaymentAttested` event from the configured ledger, matching the
  current task, payer, amount and settlement reference.
- Queries the actual `submitEvidence` transaction and validates exactly
  one `EvidenceAnchored` event from that ledger, matching task and
  SHA-256 hash, submitted by creator or trusted Recorder.
- Checks each transaction receipt's block hash against the **current**
  RPC canonical block at the same height; wrong/missing/reorged receipts
  are rejected.
- Verifies strict **USDC settlement → Recorder payment attestation →
  Evidence anchor** transaction chronology, using block number and
  within-block transaction index. Missing indexes, reordered events, or
  duplicate transaction hashes fail closed, including when two valid
  transactions appear in the same block.
- Before declaring an observed PASS, **rechecks all three canonical
  block hashes** and re-reads the live task once more. If a reorg or task
  drift happens during verification, report an uncertain outcome and
  do not recharge.
- Emits only a factual observation report and **never** signs,
  broadcasts, changes the journal, or modifies ACP execution state.

The output `live-observation.json` includes the Testnet transaction
hashes, block hashes, settlement reference, Evidence hash, and explicit
`finalityProven:false`, `serviceDeliveryProven:false`,
`sdkReady:false`. The output is created with exclusive write semantics,
so a later observation must get a new filename rather than overwriting
an earlier snapshot.

**Block canonicality is only checked at observation time**, not a
cryptographic guarantee of irreversibility. Independently verify the
recorded hashes with another provider and the chain's actual finality
policy before trusting funds or authorizing retries/refunds.

### Failure meanings

| Failure | Safe outcome |
| --- | --- |
| PaymentUnknown, missing original receipt or unknown nonce | No acceptance; do NOT retry payment |
| Mismatched USDC event, block changed, or stale snapshot | No acceptance; investigate/reconcile |
| Recorder event missing or wrong settlement reference | No acceptance; verify authorized recorder |
| Evidence event missing, tampered bytes or wrong submitter | No acceptance; inspect ACP outcome and evidence |
| Wrong chain, Anvil, unpinned RPC, missing contract code | No acceptance; no success artifact |
| Verified events with no finality proof | `PASS_OBSERVED_NOT_FINALITY`, **never** mainnet/production-ready |

## Hermetic CI and remaining Phase 0 gate

```bash
cd experiments/agentledger/examples/verifier
npm test
```

CI deliberately uses generated event fixtures and mocked RPC calls,
not wallets, USDC transfers, mainnet funds, or third-party facilitators.

Phase 0 is still **not publicly paid end-to-end accepted** until a real
Monad Testnet wallet flow and the above verified public-chain evidence
exist. If none exists, report `NOT_RUN_LIVE_TESTNET`, not PASS.
Only after independently reviewed true live acceptance and ACP owner
boundaries should a stable **read-only** receipt/Evidence verification SDK
be considered. Do not SDK-ize signing, settlement or execution ownership.

## Event-ordering and observation window

A matching event is necessary but not sufficient: a genuine payer
authorization must precede `PaymentAttested`, and the Recorder
attestation must precede `EvidenceAnchored`. The live verifier
checks ordered pair `(blockNumber, transactionIndex)` for each of
the three transactions; a missing transaction index is **not**
interpreted as zero. It also rejects any reused transaction hash
across these three independent event types.

Canonical headers are validated once while examining each receipt,
then fetched **again at the end** before writing the immutable report.
The on-chain task is also re-read at the end, and its payer, amount,
settlement reference, evidence hash, and Proven state must still agree.
This closes a basic read-skew/reorg window, but does not provide a
cryptographically atomic multi-RPC snapshot or chain finality.

A valid observation remains weaker than independently verified
execution ownership. `PaymentUnknown` is the default whenever
the wallet charge or canonical chain outcome cannot be established.
Do not release a reserved budget, re-sign a payment, or start a
new charge based on a failed read-only acceptance.

### Remaining trust assumptions

- The caller supplies the expected deployed contract address and
  original authorization. The verifier checks that address and events,
  **not** complete deployed-bytecode provenance or source verification.
  Validate the deployment provenance separately.
- The pinned RPC is one observation source, not an independent
  multi-provider quorum or finality oracle.
- Nothing in `PaymentAttested` proves USDC by itself: real USDC
  `Transfer` and EIP-3009 `AuthorizationUsed` remain mandatory.
- Nothing in `EvidenceAnchored` proves a correct Agent business outcome:
  ACP owns independent-execution-proof and OutcomeContract.
