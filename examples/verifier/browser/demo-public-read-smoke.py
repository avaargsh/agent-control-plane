#!/usr/bin/env python3
"""Complete local guide, then real public Testnet read. No wallet/signing adapter.

Requires the development service already running. Offline browser tests use
studio-browser-smoke.mjs instead; this check deliberately contacts the public
RPC through the local read-only API and fails if it cannot verify the showcase.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

url = os.environ.get('AGENTLEDGER_DEMO_URL', 'http://127.0.0.1:4322/demo')
assert urlparse(url).hostname in {'127.0.0.1', 'localhost'}, 'Development service must remain loopback'
output = Path(__file__).resolve().parents[3] / 'out' / 'demo-complete'
output.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('AGENTLEDGER_CHROME_PATH', '/usr/bin/google-chrome'),
                                headless=True, args=['--no-sandbox'])
    try:
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        errors, methods = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: methods.append(request.method))
        page.goto(url, wait_until='networkidle')
        assert page.evaluate('typeof window.ethereum === "undefined"')
        assert page.locator('#network-indicator').get_attribute('data-state') == 'configured'
        page.screenshot(path=str(output / 'overview-desktop.png'), full_page=True)
        for index, expected in enumerate(['Created', 'Paid', 'Proven', 'Proven', 'Proven', 'Paid']):
            page.locator('#guide-next').click()
            expect(page.locator('#guide-counter')).to_contain_text(f'{index + 1} / 7')
            expect(page.locator('#chain-state')).to_have_text(expected)
            if index == 2:
                expect(page.locator('#tool-words')).to_have_text('6')
            if index == 3:
                expect(page.locator('#verification-message')).to_contain_text('SIMULATED MATCH')
                page.screenshot(path=str(output / 'local-evidence-verified.png'), full_page=True)
            if index == 4:
                expect(page.locator('#verification-message')).to_contain_text('EXPECTED REJECTION')
            if index == 5:
                expect(page.locator('#scenario-heading')).to_have_text('Payment response missing')
            expect(page.locator('#payment-fact')).to_have_text('PaymentUnknown')
        page.locator('#guide-next').click()
        expect(page.locator('#guide-result')).to_contain_text('Guide complete', timeout=40000)
        expect(page.locator('#creation-transaction')).to_be_visible()
        assert page.locator('#network-indicator').get_attribute('data-state') == 'verified'
        with page.expect_download() as pending:
            page.locator('#save-summary').click()
        download = pending.value
        summary = json.loads(Path(download.path()).read_text())
        download.save_as(output / 'real-task-summary.json')
        assert summary['mode'] == 'TESTNET_TASK_READ_ONLY'
        assert summary['observation']['creationReceiptChecked'] is True
        assert summary['observation']['observedAt']
        assert summary['boundaries']['isTestnetPaymentAcceptance'] is False
        assert summary['payment']['observedState'] == 'PaymentUnknown'
        assert summary['task']['ledgerState'] in {'Created', 'Paid', 'Proven'}
        page.screenshot(path=str(output / 'real-testnet-read.png'), full_page=True)
        page.screenshot(path=str(output / 'real-testnet-viewport.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        page.goto(url, wait_until='networkidle')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(output / 'overview-mobile.png'), full_page=True)
        for index in range(4):
            page.locator('#guide-next').click()
            expect(page.locator('#guide-counter')).to_contain_text(f'{index + 1} / 7')
        expect(page.locator('#verification-message')).to_contain_text('SIMULATED MATCH')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(output / 'local-proof-mobile.png'), full_page=True)
        page.screenshot(path=str(output / 'local-proof-mobile-viewport.png'))
        assert not errors, errors
        assert all(method == 'GET' for method in methods), methods
        (output / 'acceptance.json').write_text(json.dumps({
            'schemaVersion': 'agentledger.complete-demo-browser.v1',
            'localSteps': ['task', 'payment-claim', 'text-tool', 'hash-match', 'tamper-rejected', 'lost-ack'],
            'publicRead': summary,
            'checks': {'sevenStepGuide': True, 'toolCalculation': True, 'sha256Match': True,
                       'tamperRejected': True, 'publicCreationReceiptChecked': True,
                       'desktopAndMobile': True, 'noHorizontalOverflow': True},
            'walletUsed': False, 'newTransactionsSent': False, 'publicPaymentAcceptance': False,
            'pageErrors': errors,
        }, indent=2) + '\n', encoding='utf-8')
        print('COMPLETE DEMO PASS: six local proof steps + actual public creation/task read, provenance export, desktop/mobile; no wallet or new transaction. Not payment acceptance.')
    finally:
        browser.close()
