# AgentLedger TaskPaymentApproval v1 (EIP-712)

**Development-only / testnet-only. Do NOT use for real funds.**

A raw ERC-20 Transfer, x402 settlement response, and a server-selected `taskId`
do not prove that the payer approved **this specific task**. This experiment
adds an EIP-712 EOA authorization envelope to help the recorder distinguish
intent from observed fund movement.

## Binding

EIP-712 Domain:

- `name = "AgentLedger"`, `version = "1"`;
- `chainId = 10143` (Monad Testnet);
- `verifyingContract = deployed AgentLedger contract address`.

`TaskPaymentApproval` signed fields, in strict order:

1. `taskId bytes32` — unique task ID from the on-chain Created task;
2. `descriptionHash bytes32` — existing on-chain task description digest;
3. `token address` — official Monad Testnet USDC contract;
4. `payTo address` — preapproved merchant recipient;
5. `amountAtomic uint256` — exact intended charge in USDC atomic units;
6. `expiresAt uint64` — Unix seconds, at most 24 hours in the future;
7. `nonce bytes32` — per-intent random nonce (cannot be reused safely).

The **payer's wallet address** is separately recovered/verified as the
EIP-712 signature signer. The verifier currently supports **EOA signatures**;
ERC-1271 smart-contract wallets are not implemented.

The JSON envelope adds `schemaVersion`, `chainId`, `ledger`, `payer`,
and `signature`, which are checked against the signed payload and
EIP-712 domain. Example shape (placeholders, NOT valid signatures):

```json
{
  "schemaVersion": "agentledger.task-payment.v1",
  "chainId": 10143,
  "ledger": "0x...40_hex",
  "taskId": "0x...64_hex",
  "descriptionHash": "0x...64_hex",
  "token": "0xOFFICIAL_MONAD_TESTNET_USDC_ADDRESS",
  "payTo": "0x...40_hex",
  "payer": "0x...40_hex",
  "amountAtomic": "1000",
  "expiresAt": "1800000000",
  "nonce": "0x...64_hex",
  "signature": "0x...130_hex"
}
```

A wallet supporting `eth_signTypedData_v4` can create the signature
**after** displaying and confirming the exact task, recipient, network,
amount, token and expiry. The signature does not send funds.

## Manual recorder-review CLI

Requirements: Node 22+, `viem` installed in
`experiments/agentledger/examples/verifier/`, task already Created,
`approval.json` signed by the payer, and a genuine receipt notice from
x402 `receipts/<tx>.json`.

```bash
cd experiments/agentledger/examples/verifier
npm install --no-audit --no-fund --ignore-scripts
node tools/prepare-record.mjs ./approval.json \
  ../../receipts/0xTRANSACTION_HASH.json \
  0xTRUSTED_TESTNET_USDC_RECEIVER
```

The command performs **no wallet signing and no on-chain write**:

1. Read Monad Testnet chain ID, deployed contract bytecode and Created task
   (including matching descriptionHash and maxBudget).
2. Validate the EIP-712 task approval and expiry (<= 24 hours).
3. Verify on-chain Testnet USDC Transfer log and chain ID using the
   independent `settlement.mjs` receipt verifier.
4. Check task ID, approved payer, token, payee, exact amount and expected
   operator-supplied merchant payee.
5. Print `recordPayment(taskId,payer,amount,settlementRef)` candidate args
   tagged `ELIGIBLE_FOR_RECORDER_REVIEW_NOT_SUBMITTED`.

## Still missing before automated payment admission

- Store approved quotes with a durable once-only nonce reservation; signature
  verification alone is not replay protection or a wallet spending cap.
- Verify that the x402 resource challenge and fulfilled purchase correspond
  to the approved task quote, not just a task-addressed URL.
- Ensure funds are not charged repeatedly after lost ACK or a retry; maintain
  a recovery/reconciliation journal and an idempotent issuance policy.
- Decide and independently verify Monad finality/reorg policy; 2 observed
  blocks is only a basic confirmation heuristic, NOT finality proof.
- Recorder authorization / key management, refunds, disputes and recovery
  for successful payment followed by failed execution.
- Real runtime attestation and ACP independent-execution-proof linkage.

**Do not automatically broadcast `recordPayment` from this CLI.**
Neither a matching receipt nor a signature should be treated as sufficient
for production task settlement by itself.
