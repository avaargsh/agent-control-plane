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
| Tests | test/AgentLedger.t.sol + test/evidence.test.mjs | Foundry and Node tests, plus CI |
| x402 HTTP gate | examples/x402-gate/server.mjs | Standalone real x402 example; no settlement→task mapping yet |
| Facilitator check | tools/facilitator-preflight.mjs | Read-only /supported; fails closed for missing network/scheme |
| Browser UI | not implemented | CLI first, for a one-day hackathon |

**Critical distinction:** x402 /paid-data is a separately working example.
It is NOT currently wired to recordPayment or submitEvidence. Never present
a successful paid API call as a completed AgentLedger payment→execution→proof.

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
node --test test/evidence.test.mjs
forge test -vvv
node tools/facilitator-preflight.mjs
~~~

Forge was not available in the authoring environment; passing CI and local
Forge tests are required before describing the contract as verified.

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
export PAY_TO_ADDRESS=0xYOUR_TESTNET_USDC_RECEIVER
npm start
# A fresh request should return HTTP 402 (not 200):
curl -i http://127.0.0.1:4021/paid-data
~~~

An x402 v2-compatible client needs testnet USDC and a valid
PAYMENT-SIGNATURE to purchase a response. This server uses the Monad
facilitator, an explicit testnet USDC address and fail-closed /supported
checks. Its /paid-data route **does not** yet create an AgentLedger task,
durably bind settlement reference to task ID, run an Agent, or anchor evidence.

## Next milestones (not yet done)

1. P1: Bind quote/taskId and exact payment demand to x402 settlement,
   independently verify USDC Transfer log + final receipt, and durably
   reconcile retries and crash/lost ACK without double charging.
2. P1: Connect a real minimal Agent job execution and attestation generator.
3. P1: CLI or minimal Web page for task query, wallet flow, evidence upload
   and local digest comparison.
4. P2: Budget reservation, refunds/disputes, multi-recorder security, expiry,
   and integration with ACP execution authority and independent proof.

Do not merge this experimental implementation into ACP core without separate
security review, repeatable mainnet-disabled testnet acceptance evidence,
and checks for lost responses, reorgs and duplicate payments.
