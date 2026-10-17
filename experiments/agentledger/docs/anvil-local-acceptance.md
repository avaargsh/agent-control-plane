# AgentLedger — deterministic local EVM acceptance (Anvil only)

This test exercises **real Solidity transactions** and verifies both the
off-chain Evidence file and a read-only HTTP query against a **local Anvil
instance**, not the Monad Testnet.

**PAYMENT WARNING:** `recordPayment` is called by a **simulated** trusted
recorder, without USDC, x402, a payer wallet transfer, or a Facilitator.
The resulting state is only useful as a contract/API integration test. It
MUST NOT be described as a successful real-life payment or full paid-Agent
acceptance.

## Repeat locally

Prerequisites: Foundry (`forge`, `anvil`), Node.js >=22, Python 3.

```bash
cd experiments/agentledger
forge build
cd examples/verifier
npm install --no-audit --no-fund --ignore-scripts
cd ../..
anvil --host 127.0.0.1 --port 18545 --chain-id 10143 --silent &
ANVIL_PID=$!
trap 'kill "$ANVIL_PID" 2>/dev/null || true' EXIT
AGENTLEDGER_ANVIL_RPC=http://127.0.0.1:18545 \
  node examples/verifier/tools/anvil-acceptance.mjs
```

The script rejects non-local RPCs, demands local Anvil's
`anvil_setBalance` debug method, and generates fresh ephemeral developer
wallets in-process (no secrets committed). It funds only local accounts,
not public chain addresses.

### What is actually checked

1. Deploy real `AgentLedger.sol` bytecode with an explicitly selected
   recorder wallet on the local EVM.
2. `createTask` with input-file SHA-256 as `descriptionHash`; read back
   `Created` from the deployed contract.
3. Reject a local tool execution when the task is unpaid.
4. **SIMULATE a payment attestation** with local `recordPayment`;
   independently verify `Paid`, payer, amount and settlement reference.
5. Execute bounded deterministic text statistics against a Paid task;
   verify the input SHA-256 is bound to `descriptionHash`; refuse duplicate
   output files.
6. Generate canonical Evidence JSON and SHA-256, then submit
   `submitEvidence` from the authorized creator wallet on local EVM.
7. Read back `Proven` and evidence hash via viem.
8. Reproduce the digest from local Evidence/input/output bytes with the
   exact browser WebCrypto implementation; reject tampered Evidence.
9. Start the **actual local read-only verifier HTTP service**, GET
   `/api/task` and compare the on-chain `Proven` and digest.
10. Write an **explicitly local** manifest containing only transaction
    hashes, addresses, Evidence hash and acceptance flags.

Generated manifest:
`out/agentledger-anvil-acceptance.json` (GitHub Actions artifact
`agentledger-anvil-local-acceptance`). A manifest's transaction hashes
only identify local in-memory Anvil transactions: they are not public
Monad transaction proofs.

CI config: `.github/workflows/agentledger-experiment.yml`,
job `anvil-acceptance`.

## Not covered / next true milestone

- No real Monad Testnet RPC execution, faucet USDC or Testnet MON.
- No x402 HTTP 402 client-to-Facilitator payment or
  EIP-3009 `AuthorizationUsed` event.
- No real authorized human payment session or live chain finality evidence.
- No independently proven execution ownership or a general LLM Agent.
- No replay across distributed servers, refunded/failed tasks or
  production Recorder key management.

After green Anvil acceptance, the next step is a separate controlled
**Testnet-only live acceptance** using a newly created test wallet,
verifiable EIP-3009 settlement receipt, explicit recorder authorization,
executed task Evidence and independently confirmed on-chain readback.

**Keep PR #157 Draft**, do not conflate this simulated lifecycle with a
successful paid-Agent deployment.
