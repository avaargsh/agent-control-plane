# AgentLedger Phase 0 — Live Monad Testnet **observation-only** acceptance

**This verifier is read-only and non-custodial.** It cannot sign a
transaction, charge a wallet, refund, execute an Agent or advance ACP
Desired State. Its only success result is `PASS_OBSERVED_NOT_FINALITY`.

## Scope and prereqs

Phase 0 is **not** an Agent Runtime. ACP retains run ownership,
authorization, retry/resume, OutcomeContract, and independent execution
proof. Ledger is only a settlement/receipt/evidence adapter.

The following must already have happened through **separate, approved
test-only wallet operations**. The live command **also requires** a reviewed
entry in `phase0/reviewed-testnet-deployments.json`. The initial registry
contains no approved deployments and intentionally fails closed:

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

- **Requires a PR-reviewed deployment registry entry** matching the exact
  Ledger contract, its trusted Recorder, the creation transaction and
  `keccak256` of the deployed runtime bytecode. It checks that the
  deployment receipt actually created that address and that its block
  is still on the canonical chain. Unknown, substituted or unreviewed
  contracts never pass, even if their ABI/events look identical.

- Re-reads the on-chain task independently, preventing a stale verified
  projection from being used after a state change.
- Queries the actual `recordPayment` transaction and validates exactly
  one `PaymentAttested` event from the configured ledger, matching the
  current task, payer, amount and settlement reference. In addition,
  it reads the transaction's `from/to/input`: the direct caller must
  match `paymentRecorder()`, the transaction target must be the expected
  Ledger contract, and the decoded function/arguments must exactly
  match `recordPayment(taskId, payer, amount, settlementRef)`.
- Queries the actual `submitEvidence` transaction and validates exactly
  one `EvidenceAnchored` event from that ledger, matching task and
  SHA-256 hash, submitted by creator or trusted Recorder. The
  transaction's `from/to/input` must match that authorized submitter,
  the Ledger address and the exact `submitEvidence(taskId,evidenceHash)`
  selector and parameters. This Phase 0 verifier deliberately accepts
  **direct contract calls only**; multisig/proxy wrappers require a
  separately reviewed extension, not an implicit bypass.
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

## Calls as independent evidence

The Recorder event is a contract-emitted claim. The live verifier now
also examines the outer EVM transaction to verify **who invoked which
function on which contract with which parameters**. For a successful
live-observation report:

- `recordPayment` transaction: `to == ledger`,
  `from == paymentRecorder()`, expected selector and exact
  task/payer/atomic amount/settlement reference;
- `submitEvidence` transaction: `to == ledger`,
  `from == event submitter`, that submitter is the task's
  creator or trusted Recorder, expected selector and exact
  task/evidence hash;
- Each decoded event must appear once in the corresponding canonical
  receipt and its log's transaction and block must equal that receipt.
  A syntactically similar log outside that transaction is rejected.

These checks improve task attribution but do **not** make the contract's
deployed source/bytecode provenance automatically trustworthy. Verify
contract deployment against reviewed Solidity code separately. Ledger
still has no authority over ACP leases, retries, resume, execution
correctness or independent-execution-proof.

## Register a *reviewed* public Testnet deployment

This is an explicit product-security gate, not an invitation to copy
arbitrary RPC code into a whitelist. The tracked file
`experiments/agentledger/phase0/reviewed-testnet-deployments.json`
ships with `deployments: []`. Therefore no live public acceptance
is possible until a maintainer reviews and merges a specific Testnet
deployment record.

After a dedicated Testnet deployment has occurred, independently gather
the following values from official RPC and the Foundry compilation artifacts:

- exact contract address and direct creator transaction hash;
- immutable configured `paymentRecorder()` address;
- runtime bytecode of the on-chain contract and
  `keccak256(runtimeBytecode)`, checked against the reviewed build
  including immutable Recorder substitution and Solidity metadata;
- matching successful contract-creation receipt with
  `contractAddress == reviewed address`, the canonical creation block,
  and a description of the build/source provenance;
- a completed PR review reference within
  `avaargsh/agent-control-plane` that approved this exact deployment.

Only after that approval should a `deployments` entry be committed:

```json
{
  "address": "0xREVIEWED_DEPLOYED_TESTNET_LEDGER",
  "paymentRecorder": "0xREVIEWED_TESTNET_RECORDER",
  "runtimeKeccak": "0x64_HEX_CHARACTER_RUNTIME_HASH",
  "deploymentTransaction": "0x64_HEX_CHARACTER_DEPLOYMENT_TX",
  "reviewReference": "https://github.com/avaargsh/agent-control-plane/pull/REVIEWED_PR_NUMBER"
}
```

The placeholders above are intentionally invalid. Do not populate this
registry from an unreviewed user request or a successful-looking
`PaymentAttested` log. **The live request JSON cannot bypass or replace
the checked-in deployment registry.**

The verifier fetches runtime code and the creation transaction and
receipt at execution time, independently checks the configured Recorder,
and rechecks the deployment block at the end along with the USDC,
Recorder and Evidence blocks. These establish only a *point-in-time
provenance match* to a reviewed record, not finality or trustworthy
Agent execution.

A registry entry with a link to a PR is not itself proof that an
independent human/source audit actually occurred. The maintainer must
verify that review before accepting the provenance assertion.
