"""Real Chromium acceptance for the loopback-only PR Watch pilot panel.

Run separately from unit tests:
    pip install -e '.[dev]' playwright==1.57.0
    python -m playwright install --with-deps chromium
    pytest -q browser_tests/test_pr_watch_panel_browser.py

No live GitHub, Temporal, public ingress, token in storage, or browser mocks
for the ordinary application API. Only the deliberate race/failure tests
intercept specific requests.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from pr_watch_service import LocalTokens, PilotHTTPServer, PilotService

TOKENS = {"alice": "a" * 40, "bob": "b" * 40}


@contextmanager
def local_pilot(db_path):
    srv = PilotHTTPServer(
        ("127.0.0.1", 0), PilotService(db_path, LocalTokens(TOKENS)),
    )
    thread = Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}"
        for who, ids in (("alice", ("pr-a", "pr-b")), ("bob", ("pr-bob",))):
            for goal_id in ids:
                data = json.dumps({
                    "goal_id": goal_id, "repo": "avaargsh/agent-control-plane",
                    "number": 169,
                }).encode("utf-8")
                request = Request(
                    base + "/api/goals",
                    data=data,
                    method="POST",
                    headers={
                        "Authorization": "Bearer " + TOKENS[who],
                        "Content-Type": "application/json",
                    },
                )
                with urlopen(request, timeout=5) as response:
                    assert response.status == 200
        yield base, srv
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    page.set_default_timeout(8000)
    try:
        yield page
    finally:
        page.close()


@pytest.fixture
def pilot(tmp_path):
    with local_pilot(tmp_path / "browser-pilot.db") as instance:
        yield instance


def connect(page, base, who="alice"):
    page.goto(base + "/", wait_until="domcontentloaded")
    page.locator("#token").fill(TOKENS[who])
    page.get_by_role("button", name="Connect").click()


def test_chromium_user_switch_isolates_goals_and_detail(page, pilot):
    base, _ = pilot
    connect(page, base)
    expect(page.locator("#goals .goal")).to_have_count(2)
    expect(page.locator("#goals")).to_contain_text("pr-a")
    expect(page.locator("#goals")).not_to_contain_text("pr-bob")
    page.locator("#activity-goal").select_option("pr-a")
    expect(page.locator("#task-detail strong")).to_contain_text("pr-a")
    expect(page.locator("#task-detail")).to_contain_text(
        "Business outcome independently verified: NO",
    )
    page.locator("#token").fill(TOKENS["bob"])
    page.get_by_role("button", name="Connect").click()
    expect(page.locator("#goals .goal")).to_have_count(1)
    expect(page.locator("#goals")).to_contain_text("pr-bob")
    expect(page.locator("#goals")).not_to_contain_text("pr-a")
    expect(page.locator("#task-detail")).not_to_contain_text("pr-a")
    assert page.locator("#token").input_value() == ""
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


def test_chromium_ignores_out_of_order_task_detail(page, pilot):
    base, _ = pilot
    connect(page, base)
    expect(page.locator("#goals .goal")).to_have_count(2)
    held = []
    page.route("**/api/tasks/pr-a", lambda route: held.append(route))
    page.locator("#activity-goal").select_option("pr-a")
    expect.poll(lambda: len(held)).to_be_greater_than(0)
    page.locator("#activity-goal").select_option("pr-b")
    expect(page.locator("#task-detail strong")).to_contain_text("pr-b")
    held[0].fulfill(status=200, content_type="application/json",
                    body=json.dumps({"task": {"task_id": "pr-a"}}))
    expect(page.locator("#task-detail strong")).to_contain_text("pr-b")
    expect(page.locator("#task-detail")).not_to_contain_text("pr-a")


def test_chromium_refresh_error_does_not_display_stale_task(page, pilot):
    base, _ = pilot
    connect(page, base)
    expect(page.locator("#goals .goal")).to_have_count(2)
    page.locator("#activity-goal").select_option("pr-a")
    expect(page.locator("#task-detail strong")).to_contain_text("pr-a")
    page.route("**/api/goals", lambda route: route.fulfill(
        status=503, content_type="application/json",
        body='{"error":"temporary backend outage"}',
    ))
    page.get_by_role("button", name="Refresh").click()
    expect(page.locator("#goals")).to_contain_text(
        "Unable to load tasks; retry Refresh.",
    )
    expect(page.locator("#goals .goal")).to_have_count(0)
    expect(page.locator("#task-detail")).to_contain_text(
        "Task details unavailable.",
    )
    expect(page.locator("#events")).to_contain_text("Activity unavailable.")
    expect(page.locator("#message")).to_contain_text(
        "HTTP 503: temporary backend outage",
    )


def test_chromium_pause_resume_and_cancel_confirmation(page, pilot):
    base, service = pilot
    connect(page, base)
    expect(page.locator("#goals .goal")).to_have_count(2)
    card = page.locator("#goals .goal").filter(has_text="pr-a")
    card.get_by_role("button", name="Pause").click()
    expect(card).to_contain_text("PAUSED")
    card.get_by_role("button", name="Resume").click()
    expect(card).to_contain_text("RUNNING")
    page.once("dialog", lambda dialog: dialog.dismiss())
    card.get_by_role("button", name="Cancel").click()
    expect(card.get_by_role("button", name="Cancel")).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    card.get_by_role("button", name="Cancel").click()
    expect(card).to_contain_text("CANCELLED")
    assert service.service.goals.get(user_id="alice", goal_id="pr-a").state == "CANCELLED"
    controls = service.service.goals.controls(user_id="alice", goal_id="pr-a")
    assert [control["action"] for control in controls] == [
        "pause", "resume", "cancel",
    ]
