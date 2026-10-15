# AgentLedger — Pay → Execute → Prove (Monad testnet experiment)

**Status: hackathon prototype; NOT production-ready; not an ACP v0.1 protocol change.**

AgentLedger is an isolated experiment in the Agent Control Plane repository.
It combines a task ledger, an experimental Monad Testnet x402 paid endpoint,
and a content-addressed evidence CLI. It is deliberately outside src/, release/,
and the frozen v0.1 acceptance contract.

## What exists now

| Component | Implementation | Important limit |
| --- | --- | --- |
| Solidity task ledger | src/AgentLedger.sol | Stores a trusted recorder's PAYMENT ATTESTATION, not independently verified USDC |
| Lifecycle | Created → Paid → Proven | No durable offchain Executing / Failed / Expired orchestration yet |
| Evidence | tools/evidence.mjs | SHA-256 of canonical metadata; NOT proof of truthful execution |
| Tests | test/AgentLedger.t.sol + Node evidence/settlement tests | Foundry and Node tests, plus CI |
| x402 HTTP gate | examples/x402-gate/server.mjs | Fixed task-ID route; journals unverified settlement notices, no ledger mutation |
| Facilitator check | tools/facilitator-preflight.mjs | Read-only /supported; fails closed for missing network/scheme |
| On-chain receipt verifier | tools/settlement.mjs | Read-only chain/USDC/log verification, NOT payment intent authorization |\n| Task payment intent | examples/verifier/lib/payment-approval.mjs | EIP-712 payer signature verification; no automatic payment admission |
| Durable admission journal | tools/admission-journal.py | SQLite PREPARED/UNKNOWN, replay/nonce conflict fences; no payment broadcast |
| Off-chain execution demo | examples/verifier/tools/run-demo-task.mjs | Paid-only deterministic tool with input binding and Evidence output |
| Browser verifier UI | examples/verifier/ | Read-only task query, browser-local Evidence SHA-256; no signing or payments |

**Critical distinction:** x402 payments can be scoped to a fixed task path, but
settlement-to-**authorized**-task binding is not yet implemented. The local
journal captures unverified settlement notices. It is NOT wired to
recordPayment or submitEvidence. Never present a paid API response as
AgentLedger payment→execution→proof. See [receipt reconciliation](docs/receipt-reconciliation.md).

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
export PAY_TO_ADDRESS=0xYOUR_TESTNET_USDC_RECEIVER
npm start
# A fresh request should return HTTP 402 (not 200):
curl -i "http://127.0.0.1:4021/tasks/$TASK_ID/run"
~~~

An x402 v2-compatible client needs testnet USDC and a valid
PAYMENT-SIGNATURE to purchase a response. This server uses the Monad
facilitator, an explicit testnet USDC address and fail-closed /supported
checks. Its task-scoped route journals **unverified** payment responses; it
still **does not** cryptographically bind a payer-approved quote to the task,
call recordPayment, run an Agent, or anchor execution evidence.

The read-only verification path is documented in
[docs/receipt-reconciliation.md](docs/receipt-reconciliation.md).

## Next milestones (not yet done)

1. P1: Bind signed quote/taskId and payer-approved payment intent to x402
   settlement; use the new read-only USDC Transfer receipt verifier as one
   input to durable, idempotent payment reconciliation (not yet built).
2. P1: Connect a real minimal Agent job execution and attestation generator.
3. P1: CLI or minimal Web page for task query, wallet flow, evidence upload
   and local digest comparison.
4. P2: Budget reservation, refunds/disputes, multi-recorder security, expiry,
   and integration with ACP execution authority and independent proof.

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
