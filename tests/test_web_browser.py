from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from epipilot.web.app import create_app
from epipilot.web.service import WorkbenchService
from test_web_repository import make_repo

pytestmark = pytest.mark.skipif(
    os.environ.get("EPIPILOT_BROWSER_TESTS") != "1",
    reason="Set EPIPILOT_BROWSER_TESTS=1 with the browser extra and Chromium installed",
)


@pytest.fixture
def live_url(tmp_path: Path) -> Iterator[str]:
    repo = make_repo(tmp_path / "repo")
    server = uvicorn.Server(
        uvicorn.Config(create_app(WorkbenchService(repo)), log_level="error", ws="none")
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "test web server did not start"
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)


def test_workbench_navigation_search_and_backend_handoff(live_url: str, tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("EPIPILOT_CHROMIUM"),
            args=["--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_url)
        expect(page.get_by_role("heading", name="先看懂项目，再决定下一步。")).to_be_visible()
        expect(page.get_by_role("tab")).to_have_count(5)
        page.get_by_role("tab", name="项目认知").click()
        expect(page.get_by_text("Coordinates project execution.", exact=True)).to_be_visible()
        page.get_by_role("tab", name="项目认知").focus()
        page.keyboard.press("ArrowDown")
        expect(page.get_by_role("tab", name="目标推进")).to_have_attribute("aria-selected", "true")
        page.get_by_role("button", name="检索项目").click()
        page.get_by_role("searchbox", name="检索关键词").fill("Coordinates")
        page.get_by_role("button", name="检索", exact=True).click()
        expect(page.locator("#search-results .item-card")).to_have_count(1)
        page.locator("#search-results").get_by_role("button", name="查看依据").click()
        expect(page.get_by_role("dialog", name="src/engine.py")).to_be_visible()
        page.keyboard.press("Escape")
        page.get_by_role("tab", name="优化机会").click()
        page.get_by_label("交接包执行器").select_option("pi")
        with page.expect_download() as download_info:
            page.get_by_role("button", name="导出调查交接包").first.click()
        destination = tmp_path / "proposal.json"
        download_info.value.save_as(destination)
        proposal = json.loads(destination.read_text())
        assert proposal["executor"]["backend"] == "pi"
        assert proposal["execution_authorized"] is False
        page.get_by_role("tab", name="成果与证据").click()
        expect(page.get_by_text("尚无事件记录", exact=True)).to_be_visible()
        assert errors == []
        browser.close()


def test_mobile_layout_and_explicit_stale_state(live_url: str) -> None:
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("EPIPILOT_CHROMIUM"),
            args=["--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(live_url)
        expect(page.get_by_role("heading", name="先看懂项目，再决定下一步。")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        expect(page.get_by_role("tablist")).to_have_attribute("aria-orientation", "horizontal")
        page.route(
            "**/api/project",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":"source unavailable"}',
            ),
        )
        page.get_by_role("button", name="刷新", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("上次成功快照")
        page.get_by_role("tab", name="优化机会").click()
        expect(page.get_by_role("button", name="导出调查交接包").first).to_be_disabled()
        browser.close()
