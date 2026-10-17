# AgentLedger Phase 0 — External contract / acceptance v1

**Status:** frozen for prototype-level review, NOT production launch.
**Scope:** Monad settlement observation and Evidence hash anchoring only.
**Compatibility:** existing Solidity `Created/Paid/Proven` ABI stays unchanged;
the public Phase 0 status is a separate, **read-only** verified projection.

## 1. Product promise and state semantics

AgentLedger does **not** own the Agent's runtime lifecycle. Its externally
observable **settlement/evidence** states are:

| Public Phase 0 state | Required evidence | Safe consumer conclusion |
| --- | --- | --- |
| `Created` | Chain task Created AND a trusted, authenticated executor's `NotStarted` attestation | No known attempt was authorized by that trusted executor; Ledger never itself authorizes spending |
| `PaymentUnknown` | Attempt reserved/uncertain, receipt missing, RPC unavailable, ledger Paid without independently verified payment, or any verification uncertainty | **Outcome unknown ≠ payment failed/unpaid**. No automatic re-charge or execution permission |
| `PaymentObserved` | Signed payer quote + precharge reservation + matching on-chain USDC `Transfer` AND `AuthorizationUsed` nonce, while chain task still Created | Payment event observed; recorder on-chain payment attestation has not yet been confirmed |
| `PaymentRecorded` | The same independently checked payment event AND matching on-chain trusted recorder `Paid` or `Proven` status/settlement reference | Payment record matches observed transaction; execution outcome is outside Ledger verification |
| `EvidenceAnchored` | `PaymentRecorded` conditions plus on-chain `Proven` and independently rehashed matching local Evidence bytes | Digest matches the anchored bytes. **Not** proof that execution was correct |
| `Conflict` | Independently observed mismatch, e.g. settlement reference, payer, amount, signed quote, or uploaded Evidence digest conflict | Investigate/reconcile; never downgrade to Created or recharge |

**Unverified information is never promoted to failure or success.**

Minimal safe transition model (read-only observations):

```text
Trusted executor attests NotStarted + ledger Created
    Created
       ↓ first authorized x402 attempt reserved
    PaymentUnknown
       ↓ independently validated Transfer + EIP-3009 AuthorizationUsed
    PaymentObserved
       ↓ recorder confirms matching on-chain record
    PaymentRecorded
       ↓ canonical Evidence bytes recompute to on-chain anchor
    EvidenceAnchored

Any uncertain step   → PaymentUnknown (without releasing payment budget)
Any contradiction    → Conflict (without recharging)
```

A plain on-chain `Paid` / `Proven` state is only a **recorder
attestation**. It cannot independently prove actual USDC receipt.
Likewise `PaymentObserved` needs human/authorized recorder review and
chain-finality policy before any write; the Phase 0 projector **never**
broadcasts `recordPayment`.

`PaymentUnknown` is **not terminal**, but while unresolved:

- do NOT send a new x402 `PAYMENT-SIGNATURE`;
- do NOT release reserved budget or reuse payment/quote nonce;
- do NOT infer refund eligibility, failure, cancellation or absence of money;
- only read from RPC/transaction receipts/local precharge journal, and ask
  an authorized execution owner or human reviewer to decide whether/how to recover;
- a lost HTTP ACK can coexist with an already settled on-chain payment.

`EvidenceAnchored` means digest consistency, not successful business
execution or operation ownership. An execution owner must separately verify
its execution proof and business outcome.

**Phase 0 status is an observation, not a monotonic truth machine:**
reorgs, different RPCs, delayed writes and later conflicts may make
the projection conservatively return PaymentUnknown or Conflict. It is
safe to *re-read*; it is not safe to *re-charge*.

## 2. Two independently checkable facts

### A. Payment fact — chain event, never recorder-only claim

Retain the following evidence (no wallet secrets):

- signed task approval (EIP-712), task ID, chain ID, token, receiver,
  payer, amount, nonce, expiry and quote digest;
- x402 challenge/settlement response and the original transaction hash;
- independent Monad RPC transaction receipt, block hash/height and
  `Transfer` log by the official USDC token with exact payer/payee/amount;
- `AuthorizationUsed(authorizer,nonce)` emitted by the same USDC
  token in the same transaction; a matching **precharge reservation**;
- explicit caveat: N confirmations are a heuristic, not a formal
  chain-finality oracle. A successful receipt alone is insufficient.

Implementations: `tools/settlement.mjs`,
`examples/verifier/lib/reconcile-task.mjs`,
`examples/verifier/lib/phase0-inspect.mjs`.

### B. Evidence fact — byte digest, not Agent veracity

- chain `getTask(taskId)` evidenceHash and Proven status (recorder /
  creator submitted);
- local `agentledger.evidence.v1` canonical JSON hashed using SHA-256;
- optionally re-hash exact raw input/output bytes and compare against the
  two digests inside Evidence JSON;
- check `taskId` equality; never upload the source files to the UI server;
- the chain anchor shows **which digest was recorded**, not who reliably
  executed, what the model thought, or whether the answer is correct.

Implementations: `tools/evidence.mjs`,
`examples/verifier/public/evidence-browser.mjs`. These verifications
must remain independently inspectable even if the payment network is down.

## 3. Precise responsibility boundary: execution host vs Ledger

| Responsibility | Owner |
| --- | --- |
| Agent run / operation ID, work leases, retry, resume, task cancellation | **Execution host (outside AgentLedger)** |
| Tool execution policy, sandbox, authorization, owner fencing | **Execution host (outside AgentLedger)** |
| Business outcome, independent execution proof and its provenance | **Execution host / business owner** |
| Quote and budget admission decision, authorization to spend | **Authorized payer / budget owner** |
| Observing/validating payment events and payment-intent link | **AgentLedger adapter** |
| Idempotency record of *payment attempts*, read-only reconciliation | **AgentLedger adapter** |
| Emitting a payment record after authorized settlement verification | **Trusted settlement recorder (external, gated)** |
| Anchoring Evidence SHA-256 and returning read-only digest verification | **AgentLedger adapter** |
| Deciding whether successful payment warrants task execution or refund/retry | **External execution / business workflow** |

**Hard boundary:** AgentLedger is a settlement and Evidence observer, not an
Agent-run lifecycle, scheduler, or execution owner. Its local SQLite
payment-attempt journal is **not** an execution lease store and cannot
confer execution authority.

**No hidden coupling:** any integration may correlate `taskId` with
its own operation ID, but no Ledger call may mutate another system's desired state.
The Ledger's `PaymentUnknown` result is a signal to hold or reconcile,
not permission to retry a financial side effect.

## 4. Read-only Phase 0 inspector

```bash
cd experiments/agentledger/examples/verifier
npm install --no-audit --no-fund --ignore-scripts
node tools/phase0-inspect.mjs ./phase0-request.json
```

Example request (replace placeholders locally; do not commit receipts,
signatures or PII):

```json
{
  "schemaVersion": "agentledger.phase0.inspect.v1",
  "ledger": "0xYOUR_AGENTLEDGER_CONTRACT",
  "taskId": "0xYOUR_BYTES32_TASK_ID",
  "attempt": "Reserved",
  "payment": {
    "approval": {},
    "pendingReceipt": {},
    "expectedPayTo": "0xYOUR_APPROVED_RECEIVER"
  },
  "evidence": {
    "file": "./evidence.json",
    "inputFile": "./input.txt",
    "outputFile": "./output.json"
  }
}
```

Replace payment objects with the **actual** signed payer approval and
original pending payment notice. Omit `payment` or `evidence` when
unavailable; no source means **not independently verified**, not false.
`NotStarted` is valid only if provided by an authenticated execution owner
assertion, never inferred from missing local files. Use `Reserved` after
an attempted charge or `NotEstablished` where that fact is unknown.

The public CLI refuses a raw user-supplied `NotStarted` assertion:
only an authenticated execution integration may supply this input to the
pure projection. Missing files or journals are not authoritative
proof that no charge began.

Exit: `0` only if both payment and Evidence are independently matched;
`2` conflict, `3` unknown/incomplete chain outcome,
`4` known partial progress. Nonzero does **not** mean failed payment
or permission to retry.

## 5. Phase 0 acceptance matrix

| Test | Expected |
| --- | --- |
| Trusted executor attests NotStarted; ledger Created | Created, no automatic charge |
| x402 attempt reserved, 402/HTTP ACK lost | PaymentUnknown, **no recharge** |
| Chain RPC unavailable | PaymentUnknown, no invented outcome |
| Ledger Paid but no independently verified USDC payment event | PaymentUnknown, recorder claim visible |
| Matching Transfer + AuthorizationUsed + payer quote, ledger Created | PaymentObserved |
| Matching event, chain Paid / same settlementRef | PaymentRecorded |
| Chain Proven, payment verified, Evidence JSON SHA-256 matches | EvidenceAnchored |
| Chain Proven and Evidence matches but payment unavailable | PaymentUnknown **and** independent Evidence hash matched fact |
| Same-amount transfer, wrong nonce/recipient/asset | Not verified; PaymentUnknown/Conflict, never Passed |
| Tampered Evidence with valid payment | Conflict for digest, payment event remains independently verified |
| Simulated Anvil recorder + Evidence | Explicitly **PaymentUnknown + hash matched**; cannot claim USDC paid |

Executable test gates:

- `node --test test/phase0-status.test.mjs`
- `cd examples/verifier && npm test` (includes `phase0-inspect.test.mjs`)
- `forge test` and the `anvil-acceptance` job, which uses a
  **SIMULATED** recorder payment and emits no USDC Transfer.

## 6. SDK extraction conditions (DO NOT start early)

Only consider extracting a public SDK after:

1. a stable, independently reviewed `agentledger.phase0.status.v1`
   contract with negative-path tests (this prototype now exists);
2. a reproducible **real Monad Testnet-only x402** payment → signed
   authorization → chain receipt → recorder payment attestation →
   Evidence anchor readback, with lost ACK/nonce/cross-task mutations;
3. a completed execution-host ↔ Ledger integration boundary and proof that Ledger
   does not schedule/retry/resume execution;
4. chain finality assumptions, token/Facilitator integration, refund /
   service failure recovery and license/security review recorded.

Until then expose the **read-only inspector/protocol**, not a generalized
signing/payment SDK.


## Contract-domain isolation: taskId is not a global identifier

Each Ledger contract has its own `bytes32 taskId` namespace. Two separate
deployments can contain the **same taskId**, matching Evidence digest and
even matching on-chain settlement references. The external inspector MUST
NOT combine a payer authorization and USDC receipt for Ledger A with a
`Proven` task/Evidence anchor from Ledger B.

Phase 0 now enforces identity at three separate boundaries:

1. The task returned by `getTask` must match the requested
   `(chainId=10143, ledgerContract, taskId)`; a mismatch marks chain
   context unavailable and prevents payment or Evidence verification.
2. The original EIP-712 approval's `(ledger, taskId)` must match the
   inspected contract **before** any settlement reconciliation is attempted.
3. The independently verified reconciliation result carries
   `ledger, chainId, descriptionHash, payer, amountAtomic, taskId`.
   Both the inspector and the pure Phase 0 projection compare these
   fields against the chain-read task; a forged result or mismatched
   contract cannot become `PaymentRecorded`/`EvidenceAnchored`.

A mismatch is **`Conflict`** (or **`PaymentUnknown`** when the chain
read context itself is inconsistent). The independent Evidence byte fact
can remain visible when the *payment* quote is mismatched, but no
cross-contract payment success is inferred and no recharge is allowed.

This is orthogonal to chain finality, deployment provenance or external execution
independent execution ownership; all remain separate acceptance gates.
