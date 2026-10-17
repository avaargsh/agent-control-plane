# AgentLedger application boundaries (October 2026)

**Decision: one user application, one technical verification workbench,
one canonical read-only SDK surface.** No third demo website.

| Surface | Status | Responsibility | Path | Entry |
|---|---|---|---|---|
| **AgentLedger Pocket** | **Primary product UI** | Mobile/Web Tasks, PaymentUnknown, on-device Evidence verification, local receipt summary | `experiments/agentledger/apps/pocket/` | `npm run web` |
| **Verification Studio** | **Developer acceptance workbench, not a second product** | Read-only task lookup, full Evidence-file hashes, Lost ACK / wrong-identity / Anvil and public Testnet preflight | `experiments/agentledger/examples/verifier/` | `npm start` on localhost:4321 |
| **@agentledger/verify** | **Canonical private verification SDK alpha** | Restricted `observe / verify / reconcile`, no signing, transport injection or write-chain helpers | `experiments/agentledger/sdk/` | `npm test && npm run packcheck` |
| **Extracted packages** | **Compatibility / incubator only** | Existing TS package boundaries, Python journals, Solidity parity, legacy CLI callers | `experiments/agentledger-sdk/packages/` and `apps/cli/` | `npm ci && npm run build && npm test` |
| Standalone `demo-web` | **Removed** | Purely hard-coded simulation duplicated Pocket and Studio; no unique on-chain observation, wallet or verification logic | formerly `experiments/agentledger-sdk/apps/demo-web/` | None |
| SDK duplicate verifier **static page** | **Removed** | Older static task/Evidence page duplicated Studio | formerly `experiments/agentledger-sdk/packages/agentledger-verifier/src/public/` | None |
| SDK duplicate verifier **GET API** | **Compatibility API retained** | Existing `agentledger verifier` CLI and `GET /api/task`, `GET /api/healthz`. `GET /` responds `410 LEGACY_VERIFIER_UI_RETIRED`. | `experiments/agentledger-sdk/packages/agentledger-verifier/src/server.ts` | GET-only, localhost |
| SDK monorepo nested `.github/workflows` | **Removed** | Workflow nested below project directory was not picked up by GitHub Actions | replaced by root `.github/workflows/agentledger-experiment.yml` | CI job `extracted-sdk-compat` |

## Supported entry points

For a **user-facing Pocket UI**:

```bash
cd experiments/agentledger/apps/pocket
npm install --ignore-scripts --no-audit --no-fund
npm run web
```

For **chain task inspection**, start the actual read-only Studio API separately:

```bash
cd experiments/agentledger/examples/verifier
npm install --ignore-scripts --no-audit --no-fund
npm start
# http://127.0.0.1:4321
```

For **protocol and recovery acceptance**, prefer its CLI
`tools/phase0-inspect.mjs`, `tools/demo-journey.mjs` and
`tools/live-acceptance.mjs` (the last remains blocked without a
reviewed deployed contract). The Node-only private SDK is **not** directly
bundled into Expo; Pocket uses a task-only GET adapter. The historical
extraction packages remain source-level compatibility experiments and
must not claim to supersede the hardened Phase 0 verifier.

## Boundaries, CI and migration policy

- `experiments/agentledger/` is the **active experiment source** and the
  only supported location for new Pocket/Studio UX and private
  verification SDK changes.
- `experiments/agentledger-sdk/` is **frozen for new user applications**;
  keep compatibility patches, contract parity and independent package tests
  only. Its own CLI includes an experimental x402 command and is not an
  authorization-free product API.
- The two Solidity copies currently have matching content. Root CI checks
  byte-for-byte parity; do not casually fork them or alter existing release contracts.
- Root `agentledger-experiment` CI gates both trees, including `npm ci`,
  extracted TS package builds, package tests, Python journals, and absence
  of retired app files and generated temporary fixtures.
- Future consolidation of package code requires proven behavioral parity,
  independent review and explicit migration; **do not equate an extracted
  SDK implementation with the read-only public SDK** without those gates.
- **No funded Monad Testnet acceptance:** `Paid` is a Recorder claim,
  `PaymentUnknown` never implies permission to recharge, and an
  `evidenceHash` match proves only byte equality. Execution authorization,
  retries, leases and outcome ownership remain outside AgentLedger.
- No wallet, live payment, chain mutation, PR merge or public SDK publish
  is implied by this cleanup.

## Removed tracked artifacts

The historical SDK Evidence smoke test created
`agentledger-evidence-test-*/input.txt` and `output.txt` under its
working directory. Two generated directories had been committed. Their
four files have been removed; the smoke test now uses an OS temporary
directory and cleans it in a `finally` block. A Git ignore pattern and
a CI absence check prevent accidental recommit.
