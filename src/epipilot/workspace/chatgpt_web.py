"""Authorized browser adapter that drives the ChatGPT Web chat.

Opt-in and user-initiated: the adapter launches a persistent local Chromium
profile, submits one prompt pack, waits for the reply and returns the raw text.
Driving a consumer web UI may be restricted by the provider's terms of service
and can break without notice; the adapter fails closed with explicit errors
instead of guessing at page structure. Only content the user is authorized to
disclose is ever submitted.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlparse

_DEFAULT_URL = "https://chatgpt.com/"
DEFAULT_CDP_ENDPOINT = "http://127.0.0.1:9222"
_LOGIN_ROLES = re.compile(r"log in|sign up|登录|注册|sign in", re.IGNORECASE)

_COMPOSER_SELECTORS = (
    "#prompt-textarea",
    '[data-testid="prompt-textarea"]',
    'textarea[placeholder*="Message"]',
    'div.ProseMirror[contenteditable="true"]',
    'form div[contenteditable="true"]',
    'div[contenteditable="true"]',
)


def resolve_attach_cdp_url(configured: str | None, probe: Callable[[str], bool]) -> str | None:
    """Resolve the CDP attach endpoint.

    Auto-iteration drives the user's own logged-in Chrome by default whenever a
    local CDP endpoint answers. An explicit value forces that endpoint; values
    ``off``/``none``/empty disable attach and select the isolated instance.
    """
    if configured is not None:
        value = configured.strip()
        if value.lower() in {"", "off", "none"}:
            return None
        return value
    return DEFAULT_CDP_ENDPOINT if probe(DEFAULT_CDP_ENDPOINT) else None


class ChatGPTWebError(RuntimeError):
    """Raised when the ChatGPT Web session cannot be driven as requested."""


def _balanced_json_objects(text: str) -> list[str]:
    objects: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start : index + 1])
                start = None
    return objects


def extract_route_review_json(text: str) -> str:
    """Return the first JSON object in the response that looks like a route review."""
    for candidate in _balanced_json_objects(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "verdict" in parsed:
            return candidate
    raise ChatGPTWebError("No route-review JSON object found in the ChatGPT response")


@dataclass(frozen=True, slots=True)
class ChatGPTWebSettings:
    """Browser-session settings for the ChatGPT Web adapter."""

    profile_dir: Path
    url: str = _DEFAULT_URL
    headless: bool = False
    # A ChatGPT Web experiment may legitimately run for a long time. Keep the
    # isolated/CDP fallback on the same two-hour reply window as the extension
    # bridge so a temporary bridge disconnect does not shorten the contract.
    timeout_seconds: float = 7200.0
    # Prefer the system Google Chrome: automation-built Chromium is rejected by
    # some sites (incl. ChatGPT) with a "browser not safe" wall. Empty string
    # selects Playwright's bundled Chromium instead.
    browser_channel: str = "chrome"
    # How long the opened browser may wait for a one-time manual login before
    # failing closed. 0 disables the wait (composer must be ready immediately).
    login_grace_seconds: float = 600.0
    # Explicit browser binary (native Google Chrome) removes any ambiguity about
    # which browser is driven; when None the configured channel is used instead.
    browser_executable: str | None = None
    # When set, attach over CDP to a locally running Chrome (reusing its logged-in
    # session) instead of launching a separate browser. The user's browser is
    # never closed; only the tab opened for the prompt is closed.
    attach_cdp_url: str | None = None

    def __post_init__(self) -> None:
        if not self.url.startswith("https://"):
            raise ValueError("ChatGPT Web url must be https")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout must be positive")
        if self.login_grace_seconds < 0:
            raise ValueError("login grace must not be negative")
        if not str(self.profile_dir).strip():
            raise ValueError("profile dir must not be empty")
        if self.attach_cdp_url is not None:
            parsed = urlparse(self.attach_cdp_url)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
                raise ValueError("attach_cdp_url must be a local http endpoint")


class PlaywrightChatGPTDriver:
    """One-prompt driver over a persistent Playwright Chromium profile."""

    def __init__(self, settings: ChatGPTWebSettings) -> None:
        self.settings = settings

    @staticmethod
    def _wait_composer(page: Any, timeout_ms: int) -> Any | None:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            for selector in _COMPOSER_SELECTORS:
                locator = page.locator(selector).first
                with suppress(Exception):
                    if locator.is_visible():
                        return locator
            with suppress(Exception):
                page.wait_for_timeout(1000)
        return None

    @staticmethod
    def _raise_composer_missing(page: Any) -> NoReturn:
        try:
            login_button = page.get_by_role("button", name=_LOGIN_ROLES)
            if login_button.count() > 0:
                raise ChatGPTWebError(
                    "ChatGPT Web session is not logged in. Log in once in the opened "
                    "browser window (in CDP mode, log into your own Chrome), then retry."
                )
        except ChatGPTWebError:
            raise
        except Exception:
            pass
        raise ChatGPTWebError(
            "ChatGPT Web composer not found - the page UI may have changed or the "
            "session is not logged in; failing closed instead of guessing. Check the "
            "browser window, log in once, then retry."
        )

    def submit(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise ChatGPTWebError(
                "Playwright is required: pip install -e '.[chatgpt]' && playwright install chromium"
            ) from error

        settings = self.settings
        timeout_ms = int(settings.timeout_seconds * 1000)
        with sync_playwright() as playwright:
            if settings.attach_cdp_url:
                return self._submit_attached(playwright, prompt, timeout_ms)
            return self._submit_launched(playwright, prompt, timeout_ms)

    def _submit_attached(self, playwright: Any, prompt: str, timeout_ms: int) -> str:
        """Drive a locally running Chrome over CDP; never close the user's browser."""
        url = self.settings.attach_cdp_url or ""
        try:
            browser = playwright.chromium.connect_over_cdp(url)
        except Exception as error:
            raise ChatGPTWebError(
                f"Cannot attach to Chrome CDP at {url}: {error}. Fully quit Chrome, "
                "restart it with --remote-debugging-port=<port>, then retry."
            ) from error
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            return self._deliver(page, prompt, timeout_ms)
        finally:
            with suppress(Exception):
                page.close()

    def _submit_launched(self, playwright: Any, prompt: str, timeout_ms: int) -> str:
        settings = self.settings
        settings.profile_dir.mkdir(parents=True, exist_ok=True)
        launch_args: dict[str, Any] = {"headless": settings.headless}
        if settings.browser_executable:
            launch_args["executable_path"] = settings.browser_executable
        elif settings.browser_channel:
            launch_args["channel"] = settings.browser_channel
        try:
            context = playwright.chromium.launch_persistent_context(
                str(settings.profile_dir),
                **launch_args,
            )
        except Exception as error:
            raise ChatGPTWebError(
                f"Failed to launch browser channel {settings.browser_channel!r}: {error}. "
                "Install Google Chrome, or set the channel to '' to fall back to "
                "Playwright's bundled Chromium (often rejected by the site)."
            ) from error
        try:
            page = context.pages[0] if context.pages else context.new_page()
            return self._deliver(page, prompt, timeout_ms)
        finally:
            with suppress(Exception):
                context.close()

    def _deliver(self, page: Any, prompt: str, timeout_ms: int) -> str:
        settings = self.settings
        try:
            page.goto(settings.url, wait_until="domcontentloaded")
            grace_ms = int(max(settings.login_grace_seconds, 5.0) * 1000)
            composer = self._wait_composer(page, grace_ms)
            if composer is None:
                self._raise_composer_missing(page)
            try:
                composer.fill(prompt)
            except Exception:
                composer.click()
                page.keyboard.insert_text(prompt)
            page.locator('button[data-testid="send-button"]').first.click()
            stop = page.locator('button[data-testid="stop-button"]')
            with suppress(Exception):
                stop.wait_for(state="visible", timeout=5000)
            stop.wait_for(state="detached", timeout=timeout_ms)
            messages = page.locator('[data-message-author-role="assistant"]')
            return str(messages.last.inner_text())
        except ChatGPTWebError:
            raise
        except Exception as error:
            raise ChatGPTWebError(f"ChatGPT Web automation failed: {error}") from error
