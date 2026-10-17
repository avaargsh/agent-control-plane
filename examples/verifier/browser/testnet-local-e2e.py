#!/usr/bin/env python3
"""Real browser + real Solidity transactions on LOCAL Anvil, never public Testnet.

Requires the deployment helper on :14422 configured for loopback Anvil,
and Anvil on :18545 with chain ID 10143. The injected wallet is
a test adapter using Anvil's unlocked local accounts; it is not MetaMask.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright, expect

RPC = os.environ.get("AGENTLEDGER_ANVIL_RPC", "http://127.0.0.1:18545")
DEPLOY = os.environ.get("AGENTLEDGER_DEPLOY_URL", "http://127.0.0.1:14422")
for url in [RPC, DEPLOY]:
    assert urlparse(url).hostname in {"127.0.0.1", "localhost"}, "Local EVM testing only"


def request(method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    with urlopen(Request(RPC, data=body, headers={"Content-Type": "application/json"}), timeout=10) as response:
        result = json.load(response)
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    return result["result"]


assert "anvil" in request("web3_clientVersion").lower(), "Refusing non-Anvil RPC"
assert request("eth_chainId") == "0x279f"
artifacts = Path(__file__).resolve().parents[3] / "out" / "testnet-deploy"
artifacts.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("AGENTLEDGER_CHROME_PATH", "/usr/bin/google-chrome"), headless=True, args=["--no-sandbox"])
    try:
        # No extension: make the actionable missing-wallet state visible at startup.
        missing = browser.new_page(viewport={"width": 1440, "height": 1000})
        missing.goto(DEPLOY, wait_until="networkidle")
        expect(missing.locator("#wallet-help")).to_be_visible()
        expect(missing.locator("#connect")).to_be_disabled()
        missing.close()

        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce", accept_downloads=True)
        context.expose_binding("localAnvilWallet", lambda _, payload: request(
            "eth_accounts" if payload["method"] == "eth_requestAccounts" else payload["method"], payload.get("params", [])))
        context.add_init_script("""(() => {
          const provider = { isMetaMask: true, on() {}, removeListener() {},
            request: payload => window.localAnvilWallet(payload) };
          // Exercise EIP-6963 discovery WITHOUT window.ethereum.
          window.addEventListener('eip6963:requestProvider', () => window.dispatchEvent(
            new CustomEvent('eip6963:announceProvider', {detail: {
              info: {rdns: 'io.metamask', name: 'Local Anvil test adapter'}, provider
            }})));
        })();""")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(DEPLOY, wait_until="networkidle")
        page.locator("#connect").click()
        expect(page.locator("#account")).to_contain_text(request("eth_accounts")[0], ignore_case=True)
        page.locator("#prepare").click()
        expect(page.locator("#deploy")).to_be_enabled()
        page.locator("#deploy").click()
        expect(page.locator("#status")).to_contain_text("交易已提交")
        page.locator("#check").click()
        expect(page.locator("#status")).to_contain_text("部署成功")
        page.locator("#task-input").fill("Local Anvil browser test: count these words.\n")
        page.locator("#prepare-task").click()
        expect(page.locator("#create-task")).to_be_enabled()
        page.locator("#create-task").click()
        page.wait_for_url("**/studio#*", timeout=30000)
        expect(page.locator("#chain-state")).to_have_text("Created")
        expect(page.locator("#payment-fact")).to_have_text("PaymentUnknown")
        deployment = page.evaluate("JSON.parse(localStorage.getItem('agentledger.monad-testnet.deployment.v1'))")
        link = page.url
        assert "mode=live" in link and "taskId=" in link
        before = page.evaluate("JSON.parse(localStorage.getItem('agentledger.monad-testnet.deployment.v1.task'))")
        page.screenshot(path=str(artifacts / "local-evm-step4-studio.png"), full_page=True)
        # Refresh recovers exactly the same transaction, without reopening a signature.
        page.goto(DEPLOY, wait_until="networkidle")
        page.locator("#connect").click()
        expect(page.locator("#create-task")).to_be_disabled()
        page.locator("#check").click()
        expect(page.locator("#status")).to_contain_text("部署成功")
        page.locator("#check-task").click()
        expect(page.locator("#task-status")).to_contain_text("状态：Created")
        after = page.evaluate("JSON.parse(localStorage.getItem('agentledger.monad-testnet.deployment.v1.task'))")
        assert before["transactionHash"] == after["transactionHash"]
        expect(page.locator("#task-input")).to_have_value(before["input"])
        expect(page.locator("#task-input")).to_be_disabled()
        page.screenshot(path=str(artifacts / "local-evm-workbench-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(artifacts / "local-evm-workbench-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile overflow"
        # A fresh browser context has no wallet and still performs step four.
        walletless = browser.new_page()
        walletless.goto(link, wait_until="networkidle")
        assert walletless.evaluate("typeof window.ethereum === 'undefined'")
        expect(walletless.locator("#chain-state")).to_have_text("Created")
        expect(walletless.locator("#payment-fact")).to_have_text("PaymentUnknown")
        expect(walletless.locator("#query-message")).to_contain_text("Chain state loaded")
        walletless.close()
        assert not errors, errors
        (artifacts / "local-evm-browser-acceptance.json").write_text(json.dumps({
            "schemaVersion": "agentledger.local-evm-browser-acceptance.v1",
            "network": "ANVIL_LOCAL_ONLY_NOT_PUBLIC_TESTNET",
            "chainId": 10143,
            "wallet": "INJECTED_TEST_ADAPTER_NOT_REAL_METAMASK",
            "deployment": deployment,
            "task": after,
            "checks": {"runtimeAndRecorder": True, "createdTaskReadback": True,
                       "reloadPreservedTransaction": True, "restoredInput": True,
                       "studioReadOnlyLookup": True, "mobileNoOverflow": True,
                       "automaticSameOriginHandoff": True, "stepFourWithoutWallet": True},
            "payment": "PaymentUnknown",
            "publicTestnetAcceptance": False,
            "pageErrors": errors,
        }, indent=2) + "\n", encoding="utf-8")
        print("PASS LOCAL ANVIL ONLY: EIP-6963 wallet adapter → deploy → runtime/recorder check → create task → on-chain Created readback → reload recovery → Studio handoff; desktop/mobile; no public payment acceptance.")
    finally:
        browser.close()
