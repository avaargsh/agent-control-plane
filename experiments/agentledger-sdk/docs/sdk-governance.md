# AgentLedger SDK — Governance & Safety Policy

## Scope

This SDK is a structured extraction of the Monad Testnet payment experiment.
It is **experimental**, **not production-ready**, and **not an ACP v0.1 protocol change**.
It lives outside `src/`, `release/`, and the frozen v0.1 acceptance contract.

## Network boundary

- Default and only supported chain: **Monad Testnet (chainId 10143; CAIP-2 eip155:10143)**.
- Mainnet is forbidden. RPC must use HTTPS, or loophost (127.0.0.1) for local Anvil tests.
- No mainnet funds, no valuable wallets, no secrets in source.

## Custody & funds

- The `AgentLedger` contract holds **no payment assets**.
- No escrow, no refund path, no dispute protocol, no payout release, no reorg handler.
- `maxBudget` only rejects recorded amounts above the cap; it is **not** a wallet spending cap.

## Read-only by default

- All `verify*` / `inspect*` / `project*` functions are read-only.
- They never send a transaction, never sign, and never call `recordPayment` / `submitEvidence`.
- Writes require an explicit signer and are never automatic.

## Payment status semantics

| Status | Meaning | Action |
|--------|---------|--------|
| `Created` | Task exists on chain, no payment attested | Observe only |
| `PaymentUnknown` | Payment outcome uncertain | Reconcile read-only; **no recharge** |
| `PaymentObserved` | USDC Transfer + nonce independently observed | Manual recorder review |
| `PaymentRecorded` | Recorder-attested `Paid` matches observation | Do not recharge |
| `EvidenceAnchored` | Evidence digest anchored on chain | Hash equality only, not execution proof |
| `Conflict` | Observation differs from chain state | Reconcile; fail closed |

## What the SDK does NOT guarantee

- Chain finality or transaction irreversibility.
- Independent verification of USDC settlement (only Transfer + AuthorizationUsed logs).
- ACP execution ownership or independent-execution-proof correctness.
- Exactly-once charging across workstations or distributed deployments.

## Versioning

- Independent of ACP `0.1.0`. Uses pre-release semver (`0.1.0-sdk.0`).
- Breaking changes may occur in any `0.x` release until live Monad Testnet
  payment→execution→anchor acceptance is independently reviewed.

## Contributing

Changes to `experiments/agentledger-sdk/**` trigger the dedicated CI workflow.
No mainnet deployment or live payment acceptance is claimed by CI.
