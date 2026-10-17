# AgentLedger presenter script

Start the project with `npm run dev`, open <http://127.0.0.1:4322/demo>, and click **开始完整演示**. Keep MetaMask closed unless demonstrating the separate wallet workspace. The guided walkthrough only reads public-chain state in its final step.

## Three-minute route

1. **Task record, 20 seconds:** explain that a task ID and SHA-256 bind the exact input bytes. The banner says LOCAL SIMULATION. Registration does not authorize execution or spending.
2. **Payment claim, 20 seconds:** click next. Ledger Paid is a recorder assertion; independent payment remains PaymentUnknown. There is no verified USDC Transfer or AuthorizationUsed nonce.
3. **Tool result, 25 seconds:** show the word/character/byte/line counts. The browser actually computes them using the same deterministic statistics implementation as the off-chain runner. This is not an LLM/Agent run, ACP proof, or real paid execution. The public runner still refuses unpaid tasks.
4. **Evidence check, 25 seconds:** click next. The browser hashes the exact input/output and canonical Evidence JSON. The sample matches its simulated anchor. Optionally download all three sample files and upload them for an independent repeat.
5. **Tampered output, 20 seconds:** click next. One output byte changes, and verification rejects it. Payment stays unknown. Return to the previous step or use Verify simulated Evidence to recheck the original bytes.
6. **Lost response, 20 seconds:** click next. A lost ACK cannot justify another charge. Show the unchanged PaymentUnknown result. No live payment is attempted.
7. **Real public task, 40 seconds:** click next. The app reads the pinned task and its creation transaction from Monad Testnet. The response must match the contract, task ID and creator; the server checks receipt, event and block identity. Show the current Created state, creation-transaction link, last-read time and `summary.json` export. This is separate from the earlier simulated Paid/Proven story.

## Show the wallet workflow separately

Use **测试网工作台** to show connect → deploy → create → observe. Existing browser records recover the original transactions and block duplicate submissions. A new public transaction requires its own explicit wallet confirmation and test MON gas. Do not redeploy an existing contract just to repeat the presentation. Step four uses the local read-only service and automatically opens Studio after successful observation.

## Failure handling during a presentation

- **RPC unavailable:** show the error; the guide never replaces a failed public read with a simulated success. You can continue demonstrating local hashing independently and retry the public read when connectivity returns.
- **Wrong identity:** the observation clears instead of retaining old status. Copy the exact public contract and bytes32 task ID, or use the pinned shortcut.
- **File verification while changing tasks:** the verification result is bound to the original task/mode/attempt. Delayed file reads cannot update a different observation.
- **No wallet popup:** the wallet workspace shows the current request stage; handle an existing MetaMask request instead of clicking again. Read-only demo and public task lookup do not require the wallet.
- **Reset:** **重新开始** resets the demonstration only. It does not clear localStorage transaction journals, submit another transaction, or authorize payment.

## What is still outside this demo

No independently verified public USDC settlement, funded x402 end-to-end acceptance, public Paid→Proven transition, model-backed Agent run, or finality guarantee is established by this walkthrough. The reviewed deployment registry remains empty. Simulation, local calculation, Testnet task creation and full payment acceptance remain different claims.

## Validation

`npm test` runs Node, SDK and Python checks. `npm run test:browser` runs the seven-step UI with the final Testnet lookup **mocked**, including actual browser WebCrypto, file download/upload, tamper rejection, stale-proof isolation and identity-mismatch handling. `npm run test:browser:anvil` exercises actual Solidity transactions on local Anvil; see [test setup](testnet-workspace.md#local-evm-browser-test).

Run `npm run test:browser:live` with the development service running to execute the separate real public-Testnet read-only smoke. It needs Python `playwright` and system Chrome/Chromium. `AGENTLEDGER_DEMO_URL` can point to another loopback demo service; `AGENTLEDGER_CHROME_PATH` selects the browser. It deliberately fails on public RPC outages instead of falling back to fixtures. Screenshots, `real-task-summary.json` and `acceptance.json` are stored under `out/demo-complete/`. They establish only the scope stated in those reports, never funded payment acceptance.
