# AgentLedger — Pay → Execute → Prove (Monad testnet experiment)

**Status: hackathon prototype; NOT production-ready; not an ACP v0.1 protocol change.**

AgentLedger is an isolated experiment in the Agent Control Plane repository.
It combines a task ledger, an experimental Monad Testnet x402 paid endpoint,
and a content-addressed evidence CLI. It is deliberately outside src/, release/,
and the frozen v0.1 acceptance contract.

## Phase 0: stable external observation contract

**Read [Phase 0 contract](docs/phase0-contract.md) before using this
prototype as an ACP settlement adapter.** Public statuses are
`Created / PaymentUnknown / PaymentObserved / PaymentRecorded /
EvidenceAnchored / Conflict`. They are a **read-only projection of
independent payment and evidence facts**. They do not change the existing
Solidity `Created/Paid/Proven` storage status.

`PaymentUnknown` is NOT payment failure or proof of no charge: never
automatically re-charge, re-use the authorization nonce, or release a
reservation. Chain `Paid` alone is only recorder attestation and
`Proven` alone only shows a digest anchor, not independent execution
correctness.

Read-only inspection (requires live Testnet RPC and optional signed
payment/Evidence files):

```bash
cd experiments/agentledger/examples/verifier
npm install --no-audit --no-fund --ignore-scripts
node tools/phase0-inspect.mjs ./phase0-request.json
```

The existing ACP retains task ownership, execution, leases, retry/resume,
Business OutcomeContract and `independent-execution-proof.json`.
**Ledger is a settlement/attestation adapter, not a second control plane.**
Do not extract a generalized signing/payment SDK before a true
Monad Testnet payment→execution→anchor acceptance is independently verified.

## Read-only SDK alpha and visual demo (v0.2 preview)

**SDK API is narrowed to three read-only operations:**
`observe / verify / reconcile(receipt-only)`. Public module exports
contain only the factory and metadata. `client`/`transport` injection
is rejected at construction before any nonce check. The only
receipt reconciliation exposed by the SDK remains `PaymentUnknown`
until the separate Phase 0 signed-intent/precharge workflow succeeds.

**SDK:** `sdk/` now builds a private, versioned
`@agentledger/verify@0.2.0-alpha.2` package from the tested Phase 0
projection, on-chain reader and SHA-256 source modules via esbuild.
This preview is deliberately `private:true` and **not npm-published**;
it does not sign or submit payments, write Recorder attestations, manage
ACP AgentRuns or claim chain finality. Its optional on-chain USDC receipt
observation now requires an EIP-3009 `paymentNonce`, and still does
**not** verify complete signed task intent or authorize side effects.

```bash
cd experiments/agentledger/sdk
npm install --no-audit --no-fund --ignore-scripts
npm test
npm run packcheck
```

**Visual demo:** `examples/verifier/` is a responsive Verification
Studio with **Created → Paid (Recorder claim) → Evidence → Verify** and a
separate **PaymentUnknown / Lost ACK** route. The browser exports
`summary.json` including `taskId`, `descriptionHash`, chain ID,
contract, RPC identity, payment event/nonce uncertainty and Evidence hash
verification. The hermetic `tools/demo-journey.mjs` runs both paths
and cross-Ledger/wrong-payer rejection in CI.

For the exact live walkthrough and executable acceptance contract, see
[Verification Studio demo acceptance](docs/verification-studio-demo-acceptance.md).

**Visual demo:** `examples/verifier/` is now a responsive verification
studio, with distinct Task / Settlement / Evidence facts and two explicit
data sources:

- **Local Simulation:** choose Created, Lost ACK, or Evidence Anchored.
  The source label stays visible, no RPC or wallet action occurs,
  `PaymentUnknown` remains in place, and a synthetic Evidence fixture
  can be rehashed entirely in the browser.
- **Testnet lookup:** supply Ledger contract + bytes32 task ID; the
  existing GET-only API displays actual recorder-attested chain state,
  but never upgrades it to independently verified USDC payment.
  Users can select Evidence JSON, input and output files and recompute
  SHA-256 locally without uploading their content.

Start the interactive demo:

```bash
cd experiments/agentledger/examples/verifier
npm install --no-audit --no-fund --ignore-scripts
npm start
# http://127.0.0.1:4321
```

**Product boundary:** This is an AgentLedger read-only settlement /
Evidence adapter alongside ACP, not a second task control plane.
No audited public Testnet deployment exists in the default-deny
registry; real paid Testnet acceptance remains **NOT COMPLETE**. SDK
publication stays gated behind that acceptance.

## Real Monad Testnet observation gate (read-only)

A new acceptance command is available **only after** a genuine dedicated
Testnet wallet payment and separate authorized `recordPayment` /
`submitEvidence` transactions. It does **not** create any transaction.

```bash
cd experiments/agentledger/examples/verifier
npm install --no-audit --no-fund --ignore-scripts
export MONAD_RPC_URL=https://testnet-rpc.monad.xyz
export AGENTLEDGER_CHARGE_DB="$(pwd)/../../receipts/charge-attempts.db"
node tools/live-acceptance.mjs ./live-request.json ./live-observation.json
```

The command refuses localhost Anvil even with chain ID 10143; validates
the signed payer quote, on-chain USDC Transfer + EIP-3009 nonce,
pre-charge reservation, and payment block against the current canonical
block; verifies a real `PaymentAttested` Recorder event and a distinct
`EvidenceAnchored` event; checks current on-chain task state and the
SHA-256 of original Evidence, input and output bytes.

Any missing/contradictory observation leaves **no success artifact** and
must not initiate another charge. Success is strictly
`PASS_OBSERVED_NOT_FINALITY`: observed chain events, not irreversible
settlement, ACP execution ownership or business result correctness.
**No real Monad Testnet paid acceptance has yet been claimed.**

See [live Testnet observation-only runbook](docs/live-testnet-observation.md).
SDK extraction remains deferred until actual reviewed live acceptance.

## What exists now

| Component | Implementation | Important limit |
| --- | --- | --- |
| Solidity task ledger | src/AgentLedger.sol | Stores a trusted recorder's PAYMENT ATTESTATION, not independently verified USDC |
| Lifecycle | Created → Paid → Proven | No durable offchain Executing / Failed / Expired orchestration yet |
| Evidence | tools/evidence.mjs | SHA-256 of canonical metadata; NOT proof of truthful execution |
| Tests | test/AgentLedger.t.sol + Node evidence/settlement tests | Foundry and Node tests, plus CI |
| x402 HTTP gate | examples/x402-gate/server.mjs | Pre-settlement signature/quote gate and one-time charge reservation; no ledger mutation |
| Facilitator check | tools/facilitator-preflight.mjs | Read-only /supported; fails closed for missing network/scheme |
| On-chain receipt verifier | tools/settlement.mjs | Read-only chain/USDC/log verification, NOT payment intent authorization |
| Task payment intent | examples/verifier/lib/payment-approval.mjs | EIP-712 payer signature verification; no automatic payment admission |
| Durable admission journal | tools/admission-journal.py | Post-settlement SQLite PREPARED/UNKNOWN, replay/nonce conflict fences; no payment broadcast |
| Pre-charge attempt journal | tools/charge-attempt-journal.py | Before x402 settlement, atomically allows one signed payer authorization per task; fail-closed on retries |
| Off-chain execution demo | examples/verifier/tools/run-demo-task.mjs | Paid-only deterministic tool with input binding and Evidence output |
| Browser verifier UI | examples/verifier/ | Read-only task query, browser-local Evidence SHA-256; no signing or payments |
| Lost-ACK reconciliation | examples/verifier/tools/reconcile-task.mjs | No-signing review of paid Transfer + consumed nonce + precharge + chain Created/Paid/Proven |
| Local EVM lifecycle acceptance | examples/verifier/tools/anvil-acceptance.mjs | Real Anvil deployment/transactions → deterministic tool → Evidence → UI readback; payment attestation is SIMULATED |

**Critical distinction:** the HTTP gate now checks payer-signed task intent
against an EIP-3009 payment signature **before settlement**, and reserves
one local payment attempt. However, a reservation is **not a settled payment**;
the journaled settlement response is unverified until independent RPC review,
and finality/recorder admission remain separate. This demo still does NOT
auto-call recordPayment or submitEvidence. See
[precharge gate](docs/precharge-x402-gate.md) and
[receipt reconciliation](docs/receipt-reconciliation.md).

## Threat model and invariants

- Users cannot self-assert Paid: only immutable paymentRecorder can record it.
- Recorder MUST externally verify chain ID (10143), expected USDC contract,
  recipient, amount, task binding and transaction finality before recording.
- A nonzero unique settlementRef can only be consumed once across all tasks.
  Suggested reference: hash(chainId, token, txHash, transfer log index).
- The on-chain maxBudget only rejects recorded amounts exceeding it; it is
  NOT an on-chain wallet spending cap or a spending approval mechanism.
- Evidence can only be submitted by the task creator or payment recorder,
  once, and only after Paid. It is immutable thereafter.
- The creator being authorized to anchor evidence does not mean the execution
  is independently verified. ACP independent-execution-proof.json is a
  separate, stronger verification contract and is not yet mapped here.
- No task descriptions, prompts, personal data, secrets or model outputs
  are stored on-chain. Only digests are stored.
- The contract holds no payment assets. There is no escrow, refund path,
  dispute protocol, payout release, or reorg handler. Testnet only.

## Network parameters

- Network: Monad Testnet (chain ID 10143; CAIP-2 eip155:10143)
- RPC: https://testnet-rpc.monad.xyz
- Explorer: https://testnet.monadscan.com
- Test MON: https://faucet.monad.xyz
- Test USDC: Circle faucet https://faucet.circle.com
- Official Monad x402 facilitator (confirm at runtime):
  https://x402-facilitator.molandak.org
- Testnet USDC per Monad docs: 0x534b2f3A21130d7a60830c2Df862319e593943A3

Official documentation: https://docs.monad.xyz/guides/x402
x402 SDK: https://github.com/x402-foundation/x402

Never use mainnet funds or a wallet containing valuable assets for a hackathon.
Never commit mnemonic phrases or private keys.

## Local test commands (Node 22+, Foundry)

~~~bash
cd experiments/agentledger
node --test test/*.test.mjs
forge test -vvv
node tools/facilitator-preflight.mjs
cd examples/verifier
npm install --no-audit --no-fund --ignore-scripts
npm test
npm start
# Browser: http://127.0.0.1:4321
~~~

GitHub Actions has previously passed the Foundry and Node experiment jobs.
Recheck the latest commit's CI before merging; no public-chain deployment is claimed.

## Evidence walkthrough (fully local, no tokens)

~~~bash
cd experiments/agentledger
printf 'Summarize this tiny input' > /tmp/agentledger-input.txt
printf 'Short summary' > /tmp/agentledger-output.txt
# This taskId is only a fixed example, not a private key.
TASK_ID=0x$(printf '01%.0s' {1..32})
node tools/evidence.mjs create "$TASK_ID" \
  /tmp/agentledger-input.txt /tmp/agentledger-output.txt evidence.json
node tools/evidence.mjs hash evidence.json
# Paste the onchain evidenceHash into the following:
node tools/evidence.mjs verify evidence.json 0xYOUR_64_CHAR_EVIDENCE_HASH \
  /tmp/agentledger-input.txt /tmp/agentledger-output.txt
~~~

For the proof hash, we hash UTF-8 JSON encoded with exact keys, fixed
lexicographic ASCII key order, no extra whitespace, no extra keys, ISO-8601
millisecond UTC timestamps, and lowercase 0x bytes32 digests. Raw input and
output files are separately SHA-256 hashed by byte, not stored onchain.

A matching digest proves equality with the anchored bytes under these
rules, not that an Agent actually executed or that its output was correct.

## Contract deployment (testnet, manually approved wallet)

Create a **dedicated test-only account** using Foundry keystore. No secrets
should be pasted into source, scripts, GitHub, or the README.

~~~bash
cd experiments/agentledger
export MONAD_RPC_URL=https://testnet-rpc.monad.xyz
export PAYMENT_RECORDER=0xYOUR_TRUSTED_RECORDER_ADDRESS
# Install Foundry first (forge/cast) and configure its named wallet locally.
forge create src/AgentLedger.sol:AgentLedger \
  --rpc-url "$MONAD_RPC_URL" --account agentledger-demo \
  --constructor-args "$PAYMENT_RECORDER" --broadcast
# Save the returned deployed contract address:
export LEDGER=0xYOUR_DEPLOYED_CONTRACT
export TASK_ID=0xYOUR_64_CHAR_TASK_ID
export DESCRIPTION_HASH=0xYOUR_64_CHAR_DESCRIPTION_HASH
# 10,000 units = 0.01 USDC if the recorder verifies USDC has 6 decimals.
cast send "$LEDGER" 'createTask(bytes32,bytes32,uint256)' \
  "$TASK_ID" "$DESCRIPTION_HASH" 10000 \
  --rpc-url "$MONAD_RPC_URL" --account agentledger-demo
~~~

**Do not call recordPayment until you have a verified, task-bound x402
settlement receipt**. For a demo using simulated payment, use local Foundry
tests only; never label a simulated payment as a real Monad settlement.

When settlement integration is implemented, a trusted recorder calls:

~~~text
recordPayment(taskId, payer, amountAtomic, uniqueSettlementRef)
~~~

The authorized creator or recorder then anchors the verified local digest:

~~~bash
export EVIDENCE_HASH=0xYOUR_64_CHAR_EVIDENCE_SHA256
cast send "$LEDGER" 'submitEvidence(bytes32,bytes32)' \
  "$TASK_ID" "$EVIDENCE_HASH" \
  --rpc-url "$MONAD_RPC_URL" --account agentledger-demo
cast call "$LEDGER" 'getTask(bytes32)' "$TASK_ID" --rpc-url "$MONAD_RPC_URL"
~~~

## Live x402 payment-gated API (standalone milestone)

~~~bash
cd experiments/agentledger
node tools/facilitator-preflight.mjs
cd examples/x402-gate
npm install
export TASK_ID=0xYOUR_EXISTING_BYTES32_TASK_ID
export LEDGER_ADDRESS=0xYOUR_DEPLOYED_LEDGER_CONTRACT
export PAY_TO_ADDRESS=0xYOUR_TESTNET_USDC_RECEIVER
export PUBLIC_ORIGIN=http://127.0.0.1:4021
npm start
# A fresh request should return HTTP 402 (not 200):
curl -i "http://127.0.0.1:4021/tasks/$TASK_ID/run"
~~~

An x402 v2-compatible client needs testnet USDC and a valid
PAYMENT-SIGNATURE to purchase a response. This server uses the Monad
facilitator, an explicit testnet USDC address and fail-closed /supported
checks. Its task-scoped route requires a verified EIP-712 task quote and EIP-3009
x402 transfer authorization before settlement. It journals **unverified**
payment responses, but does not autonomously call recordPayment, run a
general Agent, or anchor Evidence. It does not bind the exact serialized 402
challenge bytes to the EIP-712 quote.

The read-only verification path is documented in
[docs/receipt-reconciliation.md](docs/receipt-reconciliation.md).

## Next milestones (not yet done)

1. P1: Demonstrate signed quote + x402 paid retry **live on Monad Testnet**,
   reconcile settlement to the pre-charge reservation, and prove finality
   without replaying a charge after lost ACK.
2. P1: Provide authorized test-only recordPayment and submitEvidence flow
   with transaction receipts and independent readback.
3. P1: Replace the deterministic tool demo with an actual small Agent
   runtime execution linked to ACP IndependentExecutionProof.
4. P2: Distributed budgets, refunds/disputes, multi-recorder security,
   payment recovery across instances, and production-grade audit.

Do not merge this experimental implementation into ACP core without separate
security review, repeatable mainnet-disabled testnet acceptance evidence,
and checks for lost responses, reorgs and duplicate payments.

## Read-only browser verifier (implemented)

```bash
cd experiments/agentledger/examples/verifier
npm install --no-audit --no-fund --ignore-scripts
npm test
npm start
# Navigate to http://127.0.0.1:4321
```

The UI accepts a Monad Testnet **deployed contract address** and a bytes32
**task ID**. The local Node service uses `viem` to check chain ID 10143,
confirm nonempty contract bytecode, and invoke the read-only
`getTask(bytes32)` function. It returns the status Created/Paid/Proven,
amount, budget cap, settlement reference and immutable evidence hash.

Choose the local `agentledger.evidence.v1` JSON file to reproduce the
canonical SHA-256 digest using browser WebCrypto. Optionally select
the **original input and output** files to verify both individual digests.
The UI deliberately disables MATCH until the chain task is Proven.
No file data leaves the browser. No wallet, private key, signer, or
`recordPayment` endpoint is exposed by this component.

**Trust limitations:** status Paid is a trusted recorder attestation,
not a trustless proof of USDC settlement. Evidence hash matching proves
byte equality only, not executor provenance or correct Agent execution.
No public deployment or live x402 paid run is claimed by the UI.

Static content is served from an explicit allowlist on local loopback,
with a restrictive CSP and no general-purpose RPC relay. You can change
the upstream Monad RPC through `MONAD_RPC_URL` (HTTPS or loopback only),
but cannot change the expected chain ID in the API request.

See `docs/receipt-reconciliation.md` for the remaining payment-intent
binding and idempotency gaps. Testnet only.

## Payer-signed payment intent preflight (implemented, no broadcasting)

To avoid accepting an unrelated USDC Transfer for a different task, an
EIP-712 `TaskPaymentApproval` must bind the payer's signature to the Monad
Testnet chain ID, deployed ledger contract, task ID, description hash,
recipient, official USDC, exact amount, nonce and short expiry.

`examples/verifier/tools/prepare-record.mjs` checks a payer-signed
approval against the **on-chain Created task** and independently checked
USDC receipt. It prints candidate `recordPayment` args tagged
`ELIGIBLE_FOR_RECORDER_REVIEW_NOT_SUBMITTED`. It NEVER signs or submits
a transaction and does not yet implement durable nonce reservations.

See [docs/payment-approval.md](docs/payment-approval.md) for the precise
EIP-712 struct, CLI invocation and known replay/reconciliation gaps.

## Current local acceptance slice

A local, non-custodial experiment now follows these **separate** stages:

```text
EIP-712 payer approval + Created task + verified on-chain USDC Transfer
    → SQLite PREPARED admission candidate (no automatic contract write)
    → authorized manual recorder review / recordPayment
    → independently query Paid + verify exact input SHA-256
    → deterministic off-chain text statistics tool
    → local Evidence JSON + SHA-256 (not yet anchored)
    → authorized manual submitEvidence
    → browser read-only task query + local Evidence comparison
```

**Only the local individual pieces are automated.** There is still no
truthful end-to-end public Monad Testnet payment→execution→proof acceptance.
`recordPayment` and `submitEvidence` require independently approved
wallet actions. The demo executor is deterministic and is not an LLM Agent
or ACP independent execution attestation.

- [Admission + local execution runbook](docs/admission-and-local-execution.md)
- [EIP-712 payer approval](docs/payment-approval.md)
- [Receipt verification](docs/receipt-reconciliation.md)

Repeatable tests:
```bash
cd experiments/agentledger
python3 -m unittest discover -s test -p 'test_admission_journal.py' -v
node --test test/*.test.mjs
forge test -vvv
cd examples/verifier && npm install --no-audit --no-fund --ignore-scripts && npm test
```

A persistent SQLite journal prevents conflicting **recording candidates**
within one local DB; it does **not** guarantee exactly-once x402 charges,
a distributed budget lock or replay protection across separate workstations.
The UNKNOWN status requires manual chain reconciliation, never blind retry.

## Pre-settlement x402 authorization boundary (implemented)

The task-scoped x402 demo now requires a payer-signed EIP-712
`TaskPaymentApproval` as a Base64 `X-AgentLedger-Approval` header on
**both** the challenge request and its paid retry. The server reads the
live on-chain Created task and independently verifies its description,
budget, network, payee, exact amount and payer signature. The paid retry
also checks x402 v2 `PAYMENT-SIGNATURE`, exact public resource URL, asset
and EIP-3009 USDC `TransferWithAuthorization` signature **before**
passing control to the x402 payment middleware.

A separate SQLite pre-charge journal atomically reserves **one attempt**
per task/quote nonce/payment nonce. Any retry, including an identical
paid retry after a lost ACK, is blocked and must be reconciled before
a new purchase can be safely arranged.

This is deliberately a single-workstation / single-database safety
boundary. It is **not** globally exactly-once charging or a guarantee
that a facilitator settled the payment. A reserved attempt is not a paid
task; the after-settlement receipt notice is not independently verified.
The recorder-review path and manual ledger writes remain separate.

See [signed x402 precharge runbook](docs/precharge-x402-gate.md)
and [local admission/execution runbook](docs/admission-and-local-execution.md).
Run both hermetic journal suites and the x402 gate tests:

```bash
cd experiments/agentledger
python3 -m unittest discover -s test -p 'test_*journal.py' -v
cd examples/x402-gate
npm install --no-audit --no-fund --ignore-scripts
npm test
```

**Still required for real acceptance:** a live Monad Testnet x402 402
challenge and paid retry, externally verified finality and transaction
receipts, authorized recorder contract write, real runtime completion,
Evidence anchoring and independent readback/reconciliation.

## Crash/lost-ACK read-only reconciliation

A payment may settle successfully even if the HTTP response is lost,
and a trusted recorder may record it on-chain later. **Never replay a
paid request to find out.** The verifier has a read-only recovery command:

```bash
cd experiments/agentledger/examples/verifier
npm install --no-audit --no-fund --ignore-scripts
export AGENTLEDGER_CHARGE_DB="$(pwd)/../../receipts/charge-attempts.db"
node tools/reconcile-task.mjs ./approval.json \
  ../../receipts/0xORIGINAL_PAID_TRANSACTION.json \
  0xEXPECTED_USDC_RECEIVER
```

It verifies the payer signature, consumed EIP-3009 nonce in a matching
USDC receipt, the local pre-charge reservation and the deployed ledger
task state, then returns one of:

- `SETTLEMENT_OBSERVED_LEDGER_CREATED` — receipt matches but on-chain
  task still Created: **manual recorder review**, no second charge;
- `PAID_PAYMENT_MATCH` — already recorded payment matches;
- `PROVEN_PAYMENT_MATCH` — payment record matches; Evidence still needs
  its independent bytes verification;
- **Error/UNKNOWN** — mismatch or missing input. Fail closed, investigate
  without resubmitting payment or ledger writes.

This does not assert chain finality or independently correct Agent execution.
See [lost-ACK recovery runbook](docs/lost-ack-reconciliation.md).

## Repeatable local Anvil lifecycle verification

The `anvil-acceptance` CI job compiles the actual Solidity contract,
generates ephemeral local-only accounts, deploys to an Anvil EVM configured
with chain ID 10143, creates a task and verifies it cannot execute before
payment attestation. A **simulated recorder** changes task state to Paid,
then the existing deterministic off-chain tool generates canonical Evidence.
An authorized creator submits its digest to the local contract. Viem,
browser WebCrypto and the actual read-only HTTP endpoint independently
confirm status Proven and hash equality.

The acceptance manifest is uploaded by GitHub Actions with an explicit
`ANVIL_LOCAL_SIMULATION_NOT_MONAD_TESTNET` network field and
`SIMULATED_RECORDER_ATTESTATION_NO_USDC_TRANSFER` payment mode.

**This is not a live x402 transaction.** It uses no USDC and proves
no Monad Testnet Facilitator settlement, transaction finality, true task
ownership or independent Agent runtime attestation.

See [Anvil acceptance instructions](docs/anvil-local-acceptance.md)
and the `anvil-acceptance` CI job before attempting the separate
wallet-authorized live Testnet milestone.


## Public Testnet provenance gate (default DENY)

**No contract is currently approved for a live settlement acceptance claim.**
The tracked `phase0/reviewed-testnet-deployments.json` contains an
empty `deployments` list. Phase 0 now requires a maintainer-reviewed
deployment entry **before even inspecting payments**.

For a candidate deployment, the signerless verifier independently
checks the direct creation transaction, its successful canonical
receipt, the contract address, trusted `paymentRecorder()`, and
`keccak256` of the on-chain runtime code against the checked-in
reviewed manifest. An ABI-compatible unreviewed contract cannot pass.
These tests run in the pinned `examples/verifier` dependency scope;
the pure `phase0/status.mjs` projection remains dependency-free.

This is not a claim of a completed external source audit or finality.
A URL in a manifest is not itself proof of PR approval; the recorded
deployment must actually undergo a separate code/build/deployment
review before it is allowed. The full procedure is in
[Live Testnet observation](docs/live-testnet-observation.md).

**SDK extraction remains deferred.** In particular, a valid local
Anvil simulation cannot register itself as public Monad Testnet.
