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
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

_DEFAULT_URL = "https://chatgpt.com/"
_COMPOSER_SELECTORS = (
    "#prompt-textarea",
    '[data-testid="prompt-textarea"]',
    'textarea[placeholder*="Message"]',
    'div[contenteditable="true"]',
)


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

    def __post_init__(self) -> None:
        if not self.url.startswith("https://"):
            raise ValueError("ChatGPT Web url must be https")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout must be positive")
        if self.login_grace_seconds < 0:
            raise ValueError("login grace must not be negative")
        if not str(self.profile_dir).strip():
            raise ValueError("profile dir must not be empty")


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
        login_button = page.get_by_role(
            "button", name=("log in", "sign in", "登录", "Sign up", "注册")
        )
        with suppress(Exception):
            if login_button.count() > 0:
                raise ChatGPTWebError(
                    "ChatGPT Web session is not logged in. Log in once in the opened "
                    "browser window (the session persists in the profile directory), "
                    "then retry."
                )
        raise ChatGPTWebError(
            "ChatGPT Web composer not found - the page UI may have changed; "
            "failing closed instead of guessing. Retry after checking the window."
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
        settings.profile_dir.mkdir(parents=True, exist_ok=True)
        timeout_ms = int(settings.timeout_seconds * 1000)
        launch_args: dict[str, Any] = {"headless": settings.headless}
        if settings.browser_executable:
            launch_args["executable_path"] = settings.browser_executable
        elif settings.browser_channel:
            launch_args["channel"] = settings.browser_channel
        with sync_playwright() as playwright:
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
            except Exception as error:
                raise ChatGPTWebError(f"ChatGPT Web automation failed: {error}") from error
            finally:
                context.close()
