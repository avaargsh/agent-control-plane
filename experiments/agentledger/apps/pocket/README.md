# AgentLedger Pocket — mobile-first Proof Companion

**Independent implementation inspired by the product form of
[OwenZhao9/pocket](https://github.com/OwenZhao9/pocket)**:
Expo (React Native) mobile + web from one UI, a simple onboarding path,
session journal and a wider desktop judge/evidence view.

Pocket itself is a **Fuji/Avalanche leveraged-free prediction trading demo**.
This app does **not** copy or fork its trading contracts, passkey wallet,
receipt relayer, Cloudflare server or source code. The reference repository
did not advertise a top-level software license at the time of review;
reference its UX principles, not its source files.

## What the first cut does

- **Expo iOS + Web**: welcome, Home, Tasks, Proof and Journal screens,
  responsive mobile navigation and desktop Judge View
- **Four synthetic scenarios**: Created → Recorder Paid → Evidence Anchored;
  PaymentUnknown / lost ACK remains an explicit separate scenario
- **Independent local SHA-256 check**: `expo-crypto` recomputes canonical
  `agentledger.evidence.v1` JSON plus exact input/output content hashes.
  This proves local bytes match a synthetic fixture only
- **Live, read-only Monad Testnet task lookup** against the existing Phase 0
  `GET /api/task`, with strict chain ID / contract / bytes32 identity checks
- **Summary export**: web downloads `summary.json`; iOS shares the JSON
  content via the native share sheet
- **Judge View**: wide-screen provenance, pinned RPC identity, no-recharge
  rule, cross-Ledger / wrong-payer rejection
- **No wallet in v0.1**: no passkey private-key derivation, no signing, no
  USDC charging, no gas, no task execution, no arbitrary RPC transport

## Start the app

Requires Node 22+ and a supported Expo development environment.

```bash
cd experiments/agentledger/apps/pocket
npm install --ignore-scripts --no-audit --no-fund
npm test
npm run check
npm run web       # Expo web, mobile-first layout and desktop judge view
npm run ios       # macOS/Xcode required
```

The demo works without any RPC and requires no key or wallet.

### Optional read-only task lookup

The existing verifier API is in
`experiments/agentledger/examples/verifier/`, not bundled into the app:

```bash
# Terminal 1: local read-only API, bound to loopback
cd experiments/agentledger/examples/verifier
npm install --ignore-scripts --no-audit --no-fund
AGENTLEDGER_CORS_ORIGIN=http://localhost:8081 npm start

# Terminal 2: Expo web
cd experiments/agentledger/apps/pocket
EXPO_PUBLIC_VERIFIER_API_URL=http://127.0.0.1:4321 npm run web
```

When Expo prints an origin other than `http://localhost:8081`, configure
`AGENTLEDGER_CORS_ORIGIN` to that **exact trusted local** origin, or use a
same-origin development reverse proxy. Browser CORS is not bypassed.
Native physical devices cannot reach a desktop's `127.0.0.1`; use an
explicit reachable **HTTPS read-only** verifier endpoint if native live
reads are needed. Never bind the development verifier to a public
interface solely to make the demonstration work.

The app never performs a payment, even if the live task has
`Paid` or `Proven` Recorder state: both remain `PaymentUnknown` for
independent settlement verification. The Node-only
`@agentledger/verify@0.2.0-alpha.2` from `../../sdk/` is **not imported into
the mobile bundle**, because its transport/crypto and journal assumptions
are server-side. Full EIP-712 payer intent and precharge reconciliation
remain in the separately reviewed Phase 0 verifier.

## Source/trust boundary

| Source | What it proves | What it does not prove |
| --- | --- | --- |
| Simulation | UI, deterministic hash, failure flow | Real USDC, Testnet completion |
| Live `GET /api/task` | Recorder state at a given RPC view | Actual USDC transfer, chain finality |
| Local Evidence `SHA-256` | Synthetic Evidence bytes | Business execution correctness |
| Judge View | Clear explanation of captured claims | Audit/reviewed deployment |

**Public Testnet acceptance is BLOCKED** until a reviewed deployment
is entered in the existing default-deny registry, and real
USDC Transfer + consumed EIP-3009 nonce, signed task intent,
Recorder transaction and Evidence anchor are independently verified.
The app uses `paymentStatus:PaymentUnknown`, `chargeAgain:false` and
`executionAuthority:ACP` regardless of demo state.

## Adopt → Integrate → Build

- **Adopt:** Expo/React Native, standard iOS sharing, platform crypto
- **Integrate:** existing AgentLedger Phase 0 read-only GET contract,
  existing verified SHA-256 shape and ACP authority boundary
- **Build:** original mobile UI, provenance-focused proof cards,
  identity gating and honest demo UX
- **Later, conditional:** proper passkey UX and task approval only after
  a separate security design, user consent, authenticated API,
  funded testnet acceptance and review; these are not in v0.1.

Project deliberately lives under `experiments/agentledger/apps/pocket/`
on its own branch, separate from Draft PR #157.
