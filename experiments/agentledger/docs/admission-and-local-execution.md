# Admission journal and off-chain demo execution

**Scope: hackathon prototype on Monad Testnet. No real funds or production wallets.**

This milestone connects independently verified EIP-712 payer authorization,
on-chain USDC transfer observation, a SQLite replay fence, and a minimal
deterministic tool executor. It does **not** automatically record payment or
anchor evidence. The recorder must independently review before using
`recordPayment`, and the authorized account must submit the digest.

## 1. Durable admission candidate

```bash
cd experiments/agentledger/examples/verifier
npm install --no-audit --no-fund --ignore-scripts
node tools/prepare-record.mjs \
  ./approval.json \
  ../../receipts/0xYOUR_TRANSACTION_HASH.json \
  0xYOUR_TRUSTED_TESTNET_RECEIVER
```

The CLI checks the deployed task's Created state, hash and budget; a
payer-signed EIP-712 short-lived approval; and a fresh Monad RPC USDC
receipt. It checks exact task/amount/token/payer/recipient matches, then
calls the Python 3 standard-library journal using stdin (not shell
interpolation). All failures before this step leave the database untouched.

Default DB is `experiments/agentledger/receipts/admission.db`.
`AGENTLEDGER_JOURNAL_DB` can point to another local private path.

SQLite uses `BEGIN IMMEDIATE`, WAL, FULL synchronous writes, and uniqueness
constraints over settlement reference, task, payer nonce and typed quote
digest. Repeating an **identical** candidate returns
`EXISTING_NO_NEW_SUBMISSION`. Using a consumed transaction, task ID,
approval digest or nonce for a conflicting request fails closed. Concurrent
process tests verify only one new PREPARED row is produced.

A PREPARED row means **eligible for human recorder review**. It is not a
settled or proven task, and its presence is not authority to spend money.

## 2. Lost ACK / unknown recorder outcome

A crash *after* broadcasting `recordPayment` but *before* receiving its
receipt can leave an UNKNOWN outcome.

```bash
cd experiments/agentledger
python3 tools/admission-journal.py inspect 0xYOUR_SETTLEMENT_REF
python3 tools/admission-journal.py mark-unknown 0xYOUR_SETTLEMENT_REF
```

`mark-unknown` can only transition PREPARED → UNKNOWN; idempotent repeats
stay UNKNOWN. An UNKNOWN row is **reconcile only, never resubmit or recharge**.
The operator must read Monad's actual task state, settlement reference,
transaction receipt and independent confirmation/finality evidence before
deciding whether any action remains. We intentionally provide *no*
`mark-confirmed` shortcut and no automatic broadcast.

**Limit:** A single SQLite journal on a local workstation is not a
distributed authority ledger. It cannot prevent independently running
payment clients from charging the payer twice; x402 idempotent authorization,
quote/challenge linkage, nonce reservation before wallet spending, and
provider-level reconciliation are still outstanding.

## 3. Off-chain deterministic demo task

For the one-day demo, prepare a plain UTF-8 input file and create the on-chain
task using the SHA-256 of the **raw input file bytes** as its
`descriptionHash`. The `taskId` is a distinct bytes32 unique ID.

```bash
cd experiments/agentledger
printf 'Hello Monad! One paid tool call.\n' > /tmp/agentledger-input.txt
# Bytes32 descriptionHash required when creating the on-chain task:
printf '0x'; sha256sum /tmp/agentledger-input.txt | cut -d' ' -f1
```

Once the legitimate recorder has separately verified payment and a trusted
actor has put the task on-chain in **Paid** state:

```bash
cd experiments/agentledger/examples/verifier
npm install --no-audit --no-fund --ignore-scripts
node tools/run-demo-task.mjs \
  0xYOUR_DEPLOYED_AGENTLEDGER_CONTRACT \
  0xYOUR_EXISTING_PAID_TASK_ID \
  /tmp/agentledger-input.txt \
  /tmp/agentledger-output.json \
  /tmp/agentledger-evidence.json
```

The tool checks `getTask` on Monad Testnet (chain ID 10143), refuses if
status is not Paid or descriptionHash mismatches, and executes a bounded
deterministic UTF-8 text-statistics operation. It creates both local files
with exclusive `wx` flags; a duplicate run refuses to overwrite them.
The Evidence JSON uses the existing `agentledger.evidence.v1` contract
with SHA-256 of the exact input/output file bytes.

This is an **actual off-chain deterministic tool run**, not a general
purpose Agent, and it is not independently verified ACP proof. On a crash
between result write and Evidence write, the operator must reconcile or
move the partial files; auto-retry is deliberately absent.

To anchor the returned evidence digest, the authorized creator/recorder
must explicitly call `submitEvidence(taskId,evidenceHash)` through an
approved wallet. The read-only browser UI can then query status Proven
and verify the local Evidence JSON digest. No signing material is stored
by the server.

## 4. Hermetic CI checks

```bash
cd experiments/agentledger
python3 -m unittest discover -s test -p 'test_admission_journal.py' -v
node --test test/*.test.mjs
forge test -vvv

cd examples/verifier
npm install --no-audit --no-fund --ignore-scripts
npm test
```

Acceptance constraints:

- no extra charge or journal row on identical retries;
- no cross-task/cross-nonce/cross-settlement aliasing;
- SQLite survives a new process and handles simultaneous preparations;
- failing EIP-712 signature/chain receipt never reaches admission;
- Paid state and exact raw-input SHA-256 are required to execute;
- a second result/evidence file write is refused;
- Evidence digest matches independent canonical verifier;
- no mainnet, private key, agent tool side effect, or auto-ledger mutation in CI.

## Still blocked for a truthful end-to-end Testnet demo

- Signed quote ↔ exact x402 HTTP challenge ↔ payment payload binding;
- Durable pre-charge authorization/reservation and fully reconciled
  retry/lost-ACK behavior across payment providers;
- Approved recorder identity, actual `recordPayment` and
  `submitEvidence` Testnet transaction evidence;
- Real model-backed Agent execution and ACP independent proof linkage;
- Fault-injected chain finality/reorg recovery, service failure compensation.

These are not implemented merely by passing unit tests.
