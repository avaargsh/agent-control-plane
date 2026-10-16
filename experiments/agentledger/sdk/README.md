# @agentledger/verify — v0.2.0-alpha.2

**Private, Node.js 22+, read-only SDK preview. Not published to npm.**
Only three public operations are exposed: `observe`, `verify`, and
`reconcile`. There is **no fixture parser export, injected transport,
wallet/signing helper, charge/retry method, chain-write helper or ACP
execution controller**.

```bash
cd experiments/agentledger/sdk
npm install --ignore-scripts --no-audit --no-fund
npm test
npm run packcheck
```

```js
import { createAgentLedgerVerifier } from "@agentledger/verify";
const sdk = createAgentLedgerVerifier({
  rpcUrl: "https://testnet-rpc.monad.xyz",
});
const task = await sdk.observe(contractAddress, taskId);
// task.status.state === "PaymentUnknown" when independent payment evidence
// has NOT been established, even when the on-chain recorder says Paid/Proven.

const result = await sdk.verify(contractAddress, taskId, evidenceJSON, {
  inputBytes, outputBytes
});
// result.match means canonical bytes match a live hash anchor;
// result.paymentVerified is ALWAYS false.

const receipt = await sdk.reconcile({
  network:"eip155:10143", taskId, transaction,
  payer, payTo, token, amountAtomic, paymentNonce
});
// receipt.status === "PaymentUnknown" even if Transfer + AuthorizationUsed
// events are observed. A receipt ALONE cannot bind a signed task intent.
```

`reconcile` performs **read-only receipt event + consumed EIP-3009 nonce**
checking against the configured RPC. It does **not** validate the entire
EIP-712 signed task intent, pre-charge reservation, approved Recorder or
chain finality, so it NEVER promotes the public status to Paid.
Full intent/nonce/reservation recovery remains an explicitly separate
read-only Phase 0 CLI workflow.

### Stable failures and trust boundaries

- `createAgentLedgerVerifier({client})` or
  `{transport}`, `{fetch}`, `{request}` fails at construction with
  `Injected transport/client is forbidden` **before** payment nonce
  validation or RPC activity.
- `reconcile` without `paymentNonce` fails before a network call.
- No reviewed Monad Testnet deployment is currently present in
  `phase0/reviewed-testnet-deployments.json`. **Public Testnet acceptance
  is BLOCKED**, even though local Anvil and synthetic demos work.
- `PaymentUnknown` means never automatically recharge, replay EIP-3009
  nonce, or release a reserved budget. ACP exclusively owns AgentRun
  and business OutcomeContract.
- `private:true` blocks accidental npm release. No mainnet claims,
  no actual funded Monad Testnet acceptance and no service-delivery proof.
