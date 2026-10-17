# AgentLedger extracted SDK — compatibility workspace

**Status: experimental compatibility / incubation code, not the canonical
public SDK, not production ready, and not part of ACP v0.1 protocol.**

This is a **separate TypeScript package extraction** of the experiment under
[`experiments/agentledger/`](../agentledger/README.md). It carries several
package boundaries and a legacy CLI. It must not be mistaken for the active
read-only `@agentledger/verify@0.2.0-alpha.3` SDK or used as evidence that a
funded Monad Testnet payment passed acceptance.

## Authoritative product map

See [application ownership and cleanup](../agentledger/docs/application-map.md).
There is ONE end-user application (**Pocket**) and ONE technical acceptance
workbench (**Verification Studio**), both under `../agentledger/`.
The obsolete `apps/demo-web/` and `packages/agentledger-verifier/src/public/`
frontends have been removed.

The old `agentledger verifier` CLI and
`@agentledger/verifier` compatibility server remain available for existing
consumers on loopback **GET-only**. The old `GET /` now reports HTTP 410
`LEGACY_VERIFIER_UI_RETIRED` rather than returning another UI. The
`GET /api/task` and `GET /api/healthz` APIs remain.
The `apps/cli` `x402` command is a separate experimental network service,
**not** an automatically trusted read-only wallet or payment API.

## Retained packages

| Package | Responsibility |
|---|---|
| `@agentledger/core` | Shared type/domain definitions |
| `@agentledger/evidence` | Evidence JSON hashing and verification |
| `@agentledger/settlement` | Read-only USDC receipt-event observation |
| `@agentledger/phase0` | Conservative state projection |
| `@agentledger/verifier` | Compatibility task reader / GET API |
| `@agentledger/x402` | Experimental HTTP challenge middleware |
| `agentledger-journal-py` | Experimental SQLite journal and idempotency |

```bash
cd experiments/agentledger-sdk
npm ci --ignore-scripts --no-audit --no-fund
npm run build
npm test
cd packages/agentledger-journal-py && PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Node 22 required; the workspace remains private. Its Solidity copy must
match `../agentledger/src/AgentLedger.sol` byte-for-byte, checked by root
CI. Historical Evidence fixture files are generated and deleted under the
system temporary directory; none are committed.

## Governance

- This area is in **compatibility-only mode**: fix tests and parity, but do
  not add a new Web demo, independent payment FSM, or new publicly exposed
  SDK authority. Follow [SDK governance](docs/sdk-governance.md).
- Root `.github/workflows/agentledger-experiment.yml` now executes the
  extracted SDK compatibility job on changes to either experiment tree.
  A nested `.github/workflows` directory under this subproject is not an
  active GitHub Actions workflow and has been removed.
- Monad Testnet chainId 10143 only; contract payment recording remains an
  **attestation**, not the independent USDC settlement proof.
- `PaymentUnknown` never permits recharge. No mainnet wallet, funds,
  signing action, deployment review, chain finality or ACP operation
  ownership is established by these package tests.

Source-of-truth consolidation beyond this cleanup needs separate
behavioral parity tests and review. Do not silently replace the active
read-only SDK or Pocket adapter with these package implementations.
