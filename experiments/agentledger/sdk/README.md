# @agentledger/verify — 0.2.0-alpha.1

> **Experimental, private, read-only SDK preview. NOT published; no real funded
> Monad Testnet acceptance has occurred. This is not the payment SDK.**

The package bundles previously reviewed modules from `phase0/`,
`tools/settlement.mjs`, and `examples/verifier/`; no duplicated FSM.
It exposes only task reading, conservative public status, Evidence SHA-256
verification, and a receipt-event observer.

```bash
cd experiments/agentledger/sdk
npm install --ignore-scripts --no-audit --no-fund
npm test
npm run packcheck
```

```js
import { createAgentLedgerVerifier } from "@agentledger/verify";
const verifier = createAgentLedgerVerifier({
  rpcUrl: "https://testnet-rpc.monad.xyz",
});
const status = await verifier.getStatus(contractAddress, bytes32TaskId);
// Even when the onchain recorder says Paid/Proven, without independently
// verified payer approval + USDC + reserved payment nonce:
// status.state === "PaymentUnknown".
```

`getTask(contract,taskId)` reads the deployed contract.
`getStatus(contract,taskId)` projects **PaymentUnknown** unless authoritative
ACP evidence is available elsewhere; it intentionally cannot accept an
unverified success claim as an argument.
`verifyEvidence(contract,taskId,evidence,{inputBytes,outputBytes})` compares
browser-compatible canonical SHA-256 with the live chain anchor. The returned
`paymentVerified:false` is intentional even if Evidence matches.
`verifySettlement(expected)` observes USDC Transfer + optional EIP-3009
AuthorizationUsed and the canonical block; it does **not** verify payer
EIP-712 task intent, quote reservation, or finality. Never treat it as
authorization for charging or `recordPayment`.

For stronger *combined* signed-intent reconciliation use the existing
separately audited `examples/verifier/tools/phase0-inspect.mjs` CLI and
`docs/phase0-contract.md`. **No payment API, wallet connection, automatic
retry, recorder signing, AgentRun control, or execution proof** is exported.

### Publishing gate

Version is an alpha source preview and `private:true` deliberately blocks
accidental npm publishing. Promotion requires real independently accepted
Testnet payment, reviewed contract provenance, independent ACP ownership
proof and durable failure/reorg semantics. A green simulated Anvil job alone
cannot approve a release.
