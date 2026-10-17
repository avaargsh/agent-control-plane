# AgentLedger Hackathon

AgentLedger is an isolated hackathon experiment combining a task ledger, experimental Monad Testnet x402 paid endpoint, and content-addressed evidence CLI.

**Status: hackathon prototype; NOT production-ready.**

## Run locally

Prerequisites: Node.js 22 or newer; Python 3 with `pytest` for Python tests.

```bash
npm ci --ignore-scripts --no-audit --no-fund
npm run dev
```

- **Studio:** <http://127.0.0.1:4321> — explore local simulations or query a real Monad Testnet task. No wallet is required for read-only queries.
- **Testnet workspace:** <http://127.0.0.1:4322> — connect the desktop MetaMask extension, deploy AgentLedger, check the deployment, and create a task. Every transaction requires your wallet confirmation and spends test MON for gas. Its same-origin [Studio](http://127.0.0.1:4322/studio) reads chain state without a wallet.

Use a browser with MetaMask enabled for signing. For remote development, forwarding **4322** is sufficient for the complete workspace and Studio flow; forward **4321** as well if using the standalone Studio. A phone's `127.0.0.1` does not reach the development computer; mobile QR connection is not implemented.

You can run the services separately with `npm start` and `npm run deploy:testnet`. `npm run dev` owns only its own processes and stops both on Ctrl+C. It does not terminate existing services using those ports.

## Complete demo (about three minutes)

Open **<http://127.0.0.1:4322/demo>** and click **开始完整演示**. No wallet is needed for this walkthrough.

| Step | What the audience sees | Source |
| --- | --- | --- |
| 1. Task | Input SHA-256 bound to a task ID | Local simulation |
| 2. Payment claim | Recorder says Paid; payment remains PaymentUnknown | Local simulation |
| 3. Tool result | Deterministic text statistics and downloadable input/output | Actual browser calculation; simulated ledger anchor |
| 4. Evidence | Exact input/output plus canonical Evidence SHA-256 match | Browser WebCrypto; simulated anchor |
| 5. Tampering | Changing one output byte is rejected | Actual local verification |
| 6. Lost ACK | Unknown outcome never becomes an automatic recharge | Local simulation |
| 7. Public task | The pinned creation transaction and current task are queried afresh | Real Monad Testnet read-only RPC |

The final step uses `phase0/demo-showcase.json` as a public identity reference, **not** an approval registry or cached claim of current success. RPC errors and identity mismatches remain visible failures. The **读取真实测试网任务** shortcut jumps to that check; **测试网工作台** opens wallet-assisted deployment and creation.

From the local Proven scenario you can download `evidence.json`, `input.txt`, and `output.json`, then upload them to reproduce the check. **篡改一个输出字节并验证** demonstrates rejection; verifying the original sample again restores the match. `summary.json` separates simulation from real observations and includes read time and verified creation provenance when available. The share link is read-only and carries no wallet authority.

Use **上一步**, **下一步**, or **重新开始** to present at your own pace. Restarting the walkthrough resets only the local simulation; it never deletes wallet journals or repeats a public transaction. On mobile these controls remain at the bottom of the screen. See the [presenter script](docs/demo-walkthrough.md).

## Testnet walkthrough

1. Open the Testnet workspace and connect MetaMask. The authorized address appears independently of network selection.
2. Select Monad Testnet (chain ID **10143**). Review `paymentRecorder`, which is immutable after deployment.
3. Check balance and deployment fees, then confirm the deployment in MetaMask.
4. Click **检查已提交的部署**. The workspace checks the transaction, canonical block, deployed runtime and `paymentRecorder`.
5. Prepare a task from exact UTF-8 input text and a positive budget cap. Confirm its creation in MetaMask.
6. **Step 4 runs automatically:** the local read-only service checks the creation transaction, `TaskCreated` event, canonical block and task parameters, then opens the same-origin Studio with the exact contract and task ID. Pending receipts are rechecked a bounded number of times; they never trigger another transaction. You can also click **检查任务创建交易** to recheck manually without MetaMask.

Refreshing restores the original deployment and task records. If a wallet response is lost, paste the original transaction hash into the recovery field and check it. Do not repeat a transaction just because a response is missing. Keep the downloaded deployment and task records, which include the original task input.

**Created is a task record, Paid is a recorder claim, and a hash match establishes byte consistency. None establishes independently verified USDC settlement or correct Agent execution.** The workspace does not issue payments, call `recordPayment`, or run Agents. It does not automatically add a deployment to the PR-reviewed registry.

## Phase 0 observation contract

Public status is a **read-only projection of independent payment and evidence
facts**, not part of the Solidity storage states: `Created /
PaymentUnknown / PaymentObserved / PaymentRecorded / EvidenceAnchored /
Conflict`. `PaymentUnknown` is **not** payment failure and is not proof
that no charge happened: never automatically re-charge, re-use the
authorization nonce, or release a reservation. Chain `Paid` alone is
recorder attestation; `Proven` alone is a digest anchor, not independent
execution correctness. See [Phase 0 contract](docs/phase0-contract.md)
and the [threat model](docs/payment-approval.md).

## Pocket app (read-only mobile / Web observation)

`apps/pocket/` is a standalone Expo (React Native + Web) companion for
the user-side journey **Task → payment observation → evidence check**,
with an offline four-stage demo and a Lost ACK / `PaymentUnknown`
branch. It re-verifies the exact `agentledger.evidence.v1` canonical
SHA-256 on-device via `@noble/hashes`, checks local file bytes
(2 MiB Evidence / 10 MiB input/output, strict UTF-8), queries real
Testnet tasks only through the loopback verifier's `GET /api/task`
(explicit origin-scoped CORS, no wallet, no mutations), and exports an
untrusted `summary.json`. See [apps/pocket/README.md](apps/pocket/README.md)
and the executed acceptance record
[pocket-testnet-acceptance.md](docs/pocket-testnet-acceptance.md).

```bash
cd examples/verifier && npm install --ignore-scripts --no-audit --no-fund && npm start
cd apps/pocket && npm install --ignore-scripts --no-audit --no-fund
npm run typecheck && npm test && npm run web        # http://localhost:8081
npm run export:web && npm run test:browser          # real-Chromium smoke on the export
npm run test:browser:live                           # real Testnet task, read-only via the loopback verifier
```

`test:browser:live` is the real-chain gate: it starts the loopback verifier,
reconciles the pinned Monad Testnet task server-side, drives the exported
app (desktop + 390px mobile) through `Observe Task · no gas`, and rejects
any run whose `summary.json` misrepresents the observation. The verdict is
`PASS_OBSERVED_CREATED_PAYMENT_UNKNOWN` — read-only observation, never
payment acceptance. The record is written once in
`out/pocket-live-testnet/` (`wx` flag); remove that directory to rerun.

## SDK (private preview, not published)

`sdk/` builds the private, typed `@agentledger/verify@0.2.0-alpha.3`
read-only package: three public operations `observe / verify /
reconcile(receipt-only)`, stable v1 error codes, strict pre-RPC
validation, and rejection of injected `client`/`transport` at
construction. Receipt reconciliation stays `PaymentUnknown` until the
separate Phase 0 signed-intent/precharge workflow succeeds. See
[SDK stability contract](sdk/README.md).

## Validation

```bash
npm test                 # Node + SDK + Python; failures return nonzero
npm run onsite:check     # Offline readiness.json preview; no wallet, no signature
npm run test:pocket      # Pocket TypeScript + portable SHA-256 unit tests
npm run test:pocket:typecheck
npm run test:browser     # Studio simulation in real headless Chromium
npm run test:browser:live # Optional: dev server running; real public-Testnet read, no signing
```

Pocket additionally has a **real-chain** read-only acceptance:
`apps/pocket` → `npm run test:browser:live` starts the loopback verifier,
observes the recorded Testnet task through the app, and archives
`out/pocket-live-testnet/` evidence; see the
[acceptance record](docs/pocket-testnet-acceptance.md).

Every gate above is enforced by GitHub Actions: [.github/workflows/ci.yml](.github/workflows/ci.yml)
adds the hermetic Anvil acceptance (loopback, simulated recorder payment) and the
Pocket Expo-Web export/build/smoke chain that cannot run as part of `npm test`.

Browser checks require system Chrome/Chromium; set `AGENTLEDGER_CHROME_PATH` if needed. The optional [local EVM browser test](docs/testnet-workspace.md#local-evm-browser-test) additionally deploys real Solidity bytecode and creates a real task on **local Anvil**, without claiming a public Testnet payment.

See [Testnet workspace and recovery](docs/testnet-workspace.md), [Phase 0 fact semantics](docs/phase0-contract.md), and [public Testnet acceptance requirements](docs/live-testnet-observation.md).
