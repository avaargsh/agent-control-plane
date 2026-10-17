# AgentLedger Pocket — standalone Expo mobile / Web (read-only)

This is a **Pocket-inspired** AgentLedger app: mobile Tasks / Payment /
Evidence / Journal tabs with a desktop phone frame + inspector. It is an
independent implementation inspired by
[OwenZhao9/pocket](https://github.com/OwenZhao9/pocket)'s UX structure,
not a fork of its wallet, trading or server code.

### Implemented and separately tested

- **Offline four-stage demo**: Created → Recorder-Paid claim → Evidence →
  SHA-256 verification; plus a Lost ACK / PaymentUnknown branch.
- **Real portable cryptography**: `src/evidence.ts` verifies the exact
  `agentledger.evidence.v1` canonical JSON layout using a portable
  SHA-256 function on iOS and Web. The demo expected hash is a separately
  pinned canonical vector; SHA-256 covers the original synthetic input and
  output bytes too. Tampering the digest, task, original input/output or
  canonical field set is rejected. This is **real hash verification
  against a simulated fixture**, not public-chain acceptance.
- **Task-only read-only observation**: contract + 32-byte Task ID are
  validated then fetched through the existing loopback
  `examples/verifier/server.mjs` `GET /api/task`; the adapter checks
  returned chainId=10143, contract, taskId, hashes and decimal amounts.
  The verifier has explicit local Expo Web origin-only CORS; no wildcard,
  cookies, arbitrary proxy request or contract write.
- **Evidence for a read-only Testnet task**: choose an Evidence JSON
  file with the system picker (maximum 2 MiB), optionally choose original
  binary input/output files (maximum 10 MiB each), or paste canonical
  JSON/UTF-8 input/output text. The app checks raw original bytes and
  canonical SHA-256 against the task's on-chain digest entirely on-device.
  Data is **never uploaded**. The browser picker requires direct user
  activation; the Expo native reader uses cached local files.
  Changing the task, selection or text immediately invalidates the
  previous match; selected binary files and manual text are mutually
  exclusive for the same field. Starting a replacement revokes any prior
  HASH_MATCHED state even when the picker is canceled. A malformed file
  cannot silently fall back to a formerly verified file. Reads in
  progress block verification, and an invalid replacement blocks
  verification until the user supplies a valid source or edits that field.
  A hash match still does not certify execution correctness or payment.
- **Real `summary.json` export**: on desktop, use the Inspector's
  **Export summary.json** button. On mobile, open Journal and export from
  there. Web downloads the exact file with no server upload; iOS/Android
  writes a temporary JSON file and opens the OS share sheet. The
  serializer rejects reports that claim paid USDC, permit another charge,
  report a reviewed deployment or misrepresent local simulation as a
  verified public Testnet payment. The Chromium acceptance test downloads
  and validates the actual Web file.
- **Inspector provenance**: simulated reports are marked
  `LOCAL_SIMULATION`/`rpc.contacted=false`. An observed task is marked
  `TESTNET_TASK_READ_ONLY`, never independent USDC receipt acceptance.

In both modes, chain `Paid`/`Proven` alone is **not an independently
verified USDC settlement**. Public payment state remains
`PaymentUnknown`, `chargeAgain=false`; no reviewed deployment means
`BLOCKED_NO_REVIEWED_DEPLOYMENT`. Execution authorization, recovery and business outcomes remain outside AgentLedger.

## Presenter-friendly guided journey

Pocket now provides a persistent **Guided Demo** control immediately below
the app header. It stays visible on Tasks, Payment, Evidence and Journal;
audiences no longer need to discover the four tabs manually.

1. **Start guided demo** → shows `Created`, `taskId`,
   `descriptionHash`, chain ID and contract identity.
2. **Next · Observe Recorder Paid** → switches to Payment. Contract
   `Paid` is a *Recorder attestation*. Independent USDC payment observation
   remains `PaymentUnknown` and retry remains prohibited.
3. **Next · Inspect Evidence** → switches to the Evidence tab,
   displaying the SHA-256 digest. Raw JSON, input/output files and
   on-device verification remain accessible below the guide.
4. **Next · Verify SHA-256 locally** → runs the existing real canonical
   byte-level verifier. The guide does **not advance** on a mismatch,
   failed file replacement, oversized file or unsettled picker.
5. **Next · Open audit summary** → Journal and `summary.json` export;
   every source is labelled `LOCAL_SIMULATION` or
   `TESTNET_TASK_READ_ONLY` as appropriate.
6. **Next · Show lost ACK** → returns to PaymentUnknown; absolutely
   no second charge. **Restart demo** resets the walkthrough.

The guide only drives *simulated* fixtures. A user choosing **Observe
Task** can still read a real Testnet task through the pinned read-only
service; this leaves payment and deployment review UNKNOWN/BLOCKED and
pauses the offline guide. Manually selecting a stage resets progress,
so it never leaves an old SHA-256 success attached to another task.
The transition sequence is checked in `src/demo-flow.test.ts`, and
headless Chromium clicks the whole guide, including the Lost ACK path.

**Testnet evidence provenance:** The operator may already have performed
a Monad Testnet run. That is not equivalent to the repository's
independently reviewed public paid-acceptance gate: the checked-in
`phase0/reviewed-testnet-deployments.json` has no reviewed addresses.
Bring the existing Testnet transaction hashes, contract creation/
runtime bytecode evidence, consumed Nonce, transfer/event logs and
`summary.json` to a separate technical review; this UI does not invent
those artifacts or erase a real prior run.

## Run on a local Web browser

Two terminals:

```bash
# Terminal 1: existing read-only Node service (loopback only)
cd experiments/agentledger/examples/verifier
npm install --ignore-scripts --no-audit --no-fund
npm start
```

```bash
# Terminal 2: Expo mobile/Web UI
cd experiments/agentledger/apps/pocket
npm install --ignore-scripts --no-audit --no-fund
npm run typecheck
npm test
npm run export:web
npm run test:browser   # requires system Chrome/Chromium, desktop + mobile screenshots
npm run web
```

The Expo Web dev origin is normally `http://localhost:8081`; the
local task observer defaults to `http://127.0.0.1:4321` and requires
a genuine deployed Ledger address and task ID to retrieve a live task.
The *demo* never contacts Testnet. Live task reads may fail if the
contract is not deployed; this does not permit making a paid claim.

For a different environment, `EXPO_PUBLIC_VERIFIER_URL` can specify
an HTTPS verifier service or the loopback local service. Public
verification services need their own explicit CORS/authorization and
network controls. Exposing a local service to a LAN, broadening CORS,
or passing arbitrary transport objects is not supported by this slice.
A physical iPhone cannot use the development machine's `127.0.0.1`;
native networking needs a separately deployed HTTPS service.

```bash
npm run export:web
# Built static site: dist/
```

## Test and delivery boundary

The CI `pocket-mobile-preview` job typechecks the UI, runs native/Web
portable hash, bounded file-picker and GET-only adapter tests against
a real local test HTTP server, exports Expo Web static assets, and uses
headless Chrome to click through simulated Created → Paid claim →
Evidence SHA-256 (including selected binary tampering) →
Lost ACK. It checks malformed lookups and the 390px mobile layout,
and uploads desktop/mobile screenshots as `agentledger-pocket-web-chromium`.
Chromium automation is not an iOS native build or an iPhone device
test, which remain pending.

**There is no wallet, signer, gas, USDC charge, smart contract write,
payment retry, PR-reviewed public Testnet deployment or confirmed
Testnet paid acceptance.** The `@agentledger/verify` SDK currently
targets Node and is not directly embedded in React Native. Pocket uses
a narrow observer *service adapter*, not a copy of the SDK's durable
signed-intent/payment reconciliation machinery.

## Local file safety

Expo SDK 57 uses `expo-document-picker@57` and
`expo-file-system@57` **on iOS/Android**. Native selected files are
copied to the app's local cache for immediate read. On **Web** a
persistent hidden browser `<input type=file>` is opened directly from
a user click; this avoids Expo Web's synthetic-click cancellation
behavior in headless Chromium. Both paths share the exact same raw-byte
size limits, strict UTF-8 JSON handling, and SHA-256 verification.
No file contents are uploaded.

The Chromium Web acceptance exercises the actual browser file input
with `tampered.bin` (must reject), `original.bin` (must reverify),
and an Evidence JSON fixture (must reproduce the pinned canonical
hash). It then replaces an already verified Evidence file with malformed
JSON, requires the old match to be revoked and verification disabled,
restores a valid file, and checks a new successful SHA-256 computation.
This is evidence of Web behavior, not a claim that iOS/Android document
provider implementations have been physically tested.

Native builds and physical-device tests have **not** been performed yet,
so file-provider interoperability on actual iOS/Android remains a
specific outstanding acceptance item. File selection in Expo Web is
covered by a real Chromium file-chooser test.

### Report export acceptance

Pocket's summary is an **untrusted observation artifact**, not a signing
receipt or independent execution attestation. It carries the same conservative
`PaymentUnknown`, `chargeAgain=false`, `BLOCKED_NO_REVIEWED_DEPLOYMENT`
and source identity as the on-screen inspector. `npm test` exercises
spoofed-Paid and fake-acceptance rejection; `npm run test:browser`
verifies a real `summary.json` browser download. On iOS/Android, the
`expo-sharing` flow has not yet been physically device-tested.


## Task-only browser acceptance and deployable preview

The CI browser smoke now additionally exercises a **mocked, bounded
`GET /api/task`** followed by user-supplied Evidence JSON, computes the
local SHA-256, and exports `summary.json`. It checks that the resulting
report is `TESTNET_TASK_READ_ONLY`, still `PaymentUnknown`, and
**never** changes to public paid acceptance. A second same-task-ID
response from the wrong Ledger contract is explicitly rejected and
previous Evidence verification is revoked. The mock runs entirely
inside Chromium; this is **not** an RPC or funded Testnet acceptance.

The Pocket observer also rejects HTTP redirects, non-JSON responses
and responses larger than 64 KiB, even if the server omits or lies
about `Content-Length` (the body is checked after reading). A 10s
request timeout and exact chain/contract/task identity guards remain.

The `agentledger-pocket-static-web-preview` CI artifact contains
the exported static Expo Web bundle (`dist/`) with seven-day retention.
The separate `agentledger-pocket-web-chromium` artifact contains
screenshots of the actual browser interaction. A static preview is
**not** a running public Monad app or an iOS binary, and the default
`GET` verifier endpoint must be deployed/configured separately.
