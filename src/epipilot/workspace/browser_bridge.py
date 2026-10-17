"""Chrome extension bridge: drive the user's normal, logged-in Chrome.

The extension (see ``extension/``) exchanges bounded commands with the local
backend over plain HTTP polling - a transport that survives Chrome's MV3
service-worker suspension (a WebSocket does not). Primitives: tab management,
DOM reads, clicks, typing and screenshots. Commands originate only from the
local EpiPilot process; the extension never accepts remote input and never
transmits credentials.

This solves the automation-browser login wall: ChatGPT runs in the user's own
Chrome where the session already exists.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from epipilot.workspace.chatgpt_web import _COMPOSER_SELECTORS, ChatGPTWebError

_DEFAULT_URL = "https://chatgpt.com/"
_SEND_SELECTORS = (
    'button[data-testid="send-button"]',
    'button[aria-label*="Send"]',
    'button[aria-label*="发送"]',
)
_STOP_SELECTORS = (
    'button[data-testid="stop-button"]',
    '[aria-label*="Stop"]',
    '[aria-label*="stop"]',
    '[aria-label*="停止"]',
)
# 2026 ChatGPT DOM no longer exposes message roles/test-ids; prose-classed
# 2026 ChatGPT DOM exposes conversation turns carrying a speaker label
# (e.g. "ChatGPT 说:" / "You said:") instead of role attributes (live-probed).
_ASSISTANT_SELECTORS = ('[class*="turn"]',)
_ASSISTANT_LABELS = ("ChatGPT 说", "ChatGPT said", "ChatGPT:")


def _is_assistant_turn(text: str) -> bool:
    return any(label in text[:120] for label in _ASSISTANT_LABELS)


def _strip_speaker_label(text: str) -> str:
    for label in _ASSISTANT_LABELS:
        index = text.find(label)
        if index >= 0:
            remainder = text[index + len(label) :].lstrip("\uff1a: ")
            return remainder if remainder else text
    return text


_LOGIN_SELECTORS = (
    'button[data-testid="login-button"]',
    'a[data-testid="login-button"]',
    'button[aria-label*="Log in"]',
    'button[aria-label*="登录"]',
    'a[href*="login"]',
)


class ExtensionBridgeHub:
    """Command queue and result futures for the polling Chrome extension."""

    def __init__(self, heartbeat_seconds: float = 90.0) -> None:
        self._lock = threading.Lock()
        self._queue: deque[dict[str, Any]] = deque()
        self._pending: dict[str, Future[dict[str, Any]]] = {}
        self._seq = itertools.count(1)
        self._last_seen: float = 0.0
        self._heartbeat_seconds = heartbeat_seconds
        self.extension_name: str | None = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return (
                self._last_seen > 0.0
                and (time.monotonic() - self._last_seen) < self._heartbeat_seconds
            )

    def heartbeat(self, name: str = "") -> None:
        with self._lock:
            self._last_seen = time.monotonic()
            if name:
                self.extension_name = name

    def next_command(self) -> dict[str, Any] | None:
        with self._lock:
            return self._queue.popleft() if self._queue else None

    def submit_result(self, message: dict[str, Any]) -> None:
        command_id = str(message.get("id", ""))
        with self._lock:
            future = self._pending.pop(command_id, None)
        if future is None or future.done():
            return
        if message.get("ok"):
            result = message.get("result")
            future.set_result(result if isinstance(result, dict) else {})
        else:
            future.set_exception(
                ChatGPTWebError(f"Browser extension command failed: {message.get('error')}")
            )

    def request(self, action: str, timeout: float = 120.0, **payload: Any) -> dict[str, Any]:
        """Enqueue one command and wait for the extension's result.

        Safe to call from worker threads. Fails closed when no extension has
        polled recently, so callers can fall back to other browser drivers.
        """
        if not self.connected:
            raise ChatGPTWebError(
                "EpiPilot Chrome extension is not connected. Load extension/ in "
                "chrome://extensions (developer mode, Load unpacked) and keep the "
                "local backend running."
            )
        with self._lock:
            command_id = str(next(self._seq))
            future: Future[dict[str, Any]] = Future()
            self._pending[command_id] = future
            self._queue.append({"id": command_id, "action": action, **payload})
        try:
            return future.result(timeout=timeout)
        except FutureTimeout as error:
            with self._lock:
                self._pending.pop(command_id, None)
            raise ChatGPTWebError(f"Browser extension command timed out: {action}") from error


@dataclass(frozen=True, slots=True)
class _ReplyBinding:
    conversation_url: str
    user_turn_count: int
    route_run_id: str


@dataclass(slots=True)
class ExtensionChatGPTDriver:
    """ChatGPTWebPort implemented over the EpiPilot Chrome extension."""

    hub: ExtensionBridgeHub
    url: str = _DEFAULT_URL
    # The operator-authorized reply window is two hours per submitted request.
    timeout_seconds: float = 7200.0
    poll_seconds: float = 5.0

    def submit(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        opened = self.hub.request("open_tab", url=self.url, match="chatgpt.com")
        tab_id = int(opened["tab_id"])
        deadline = time.monotonic() + self.timeout_seconds
        state = self._wait_state(tab_id, deadline)
        if state is None:
            raise ChatGPTWebError(
                "ChatGPT Web composer not found in your Chrome tab - log in once at "
                "https://chatgpt.com in that tab, then retry."
            )
        if not isinstance(state.get("assistantText"), str):
            raise ChatGPTWebError(
                "Incompatible Chrome extension reply protocol; reload the EpiPilot "
                "extension at chrome://extensions before retrying."
            )
        # Reused tabs already contain a previous assistant answer; never accept
        # it as the reply to the prompt we are about to send.
        baseline = str(state.get("assistantText") or "")
        typed = self.hub.request(
            "type", tab_id=tab_id, selectors=(state.get("composer"),), text=prompt
        )
        if not typed.get("typed"):
            raise ChatGPTWebError("Could not type into the ChatGPT composer")
        clicked = self.hub.request("click", tab_id=tab_id, selectors=list(_SEND_SELECTORS))
        if not clicked.get("clicked"):
            raise ChatGPTWebError("ChatGPT send button not found")
        return self._wait_reply(tab_id, deadline, baseline)

    def wait_existing_reply(
        self, tab_id: int, route_run_id: str, *, deadline_utc: datetime | None = None
    ) -> str:
        """Read an anchored pending reply without typing, clicking, or opening tabs."""
        deadline = time.monotonic() + self.timeout_seconds
        if deadline_utc is not None and deadline_utc.tzinfo is None:
            raise ValueError("Reply deadline must be timezone aware")
        state = self._read_before_deadline(tab_id, deadline, deadline_utc)
        url = state.get("conversationUrl")
        count = state.get("userTurnCount")
        parsed = urlparse(url) if isinstance(url, str) else None
        if (
            not isinstance(state.get("assistantText"), str)
            or not route_run_id
            or parsed is None
            or parsed.scheme != "https"
            or parsed.hostname != "chatgpt.com"
            or "/c/" not in parsed.path
            or type(count) is not int
            or count < 1
            or state.get("lastUserRouteRunId") != route_run_id
        ):
            raise ChatGPTWebError(
                "Current route binding is missing or mismatched; reload the EpiPilot "
                "extension if binding fields are missing, then inspect before retrying"
            )
        binding = _ReplyBinding(str(url), count, route_run_id)
        return self._wait_reply(tab_id, deadline, "", binding, deadline_utc)

    def screenshot(self) -> str:
        shot = self.hub.request("screenshot")
        data_url = str(shot.get("dataUrl", ""))
        if not data_url.startswith("data:image/"):
            raise ChatGPTWebError("Browser extension screenshot failed")
        return data_url

    def _read_state(self, tab_id: int, timeout: float = 120.0) -> dict[str, Any]:
        return self.hub.request(
            "read_state",
            timeout=timeout,
            tab_id=tab_id,
            composerSelectors=list(_COMPOSER_SELECTORS),
            loginSelectors=list(_LOGIN_SELECTORS),
            stopSelectors=list(_STOP_SELECTORS),
            assistantSelectors=list(_ASSISTANT_SELECTORS),
        )

    def _wait_state(self, tab_id: int, deadline: float) -> dict[str, Any] | None:
        while time.monotonic() < deadline:
            state = self._read_state(tab_id)
            if state.get("composer"):
                return state
            time.sleep(self.poll_seconds)
        return None

    @staticmethod
    def _reply_time_left(deadline: float, deadline_utc: datetime | None) -> float:
        remaining = deadline - time.monotonic()
        if deadline_utc is not None:
            remaining = min(remaining, (deadline_utc - datetime.now(UTC)).total_seconds())
        return remaining

    def _read_before_deadline(
        self, tab_id: int, deadline: float, deadline_utc: datetime | None = None
    ) -> dict[str, Any]:
        remaining = self._reply_time_left(deadline, deadline_utc)
        if remaining <= 0:
            raise ChatGPTWebError("Timed out waiting for the ChatGPT reply")
        state = self._read_state(tab_id, timeout=min(120.0, remaining))
        if self._reply_time_left(deadline, deadline_utc) <= 0:
            raise ChatGPTWebError("Timed out waiting for the ChatGPT reply")
        return state

    def _wait_reply(
        self,
        tab_id: int,
        deadline: float,
        baseline: str,
        binding: _ReplyBinding | None = None,
        deadline_utc: datetime | None = None,
    ) -> str:
        last = ""
        while time.monotonic() < deadline:
            state = self._read_before_deadline(tab_id, deadline, deadline_utc)
            if binding is not None and (
                state.get("conversationUrl") != binding.conversation_url
                or state.get("userTurnCount") != binding.user_turn_count
                or state.get("lastUserRouteRunId") != binding.route_run_id
            ):
                raise ChatGPTWebError("Pending reply binding changed; refusing unrelated response")
            text = str(state.get("assistantText") or "")
            if text and text != baseline and not state.get("stopVisible"):
                if text == last:
                    if self._reply_time_left(deadline, deadline_utc) <= 0:
                        raise ChatGPTWebError("Timed out waiting for the ChatGPT reply")
                    return text
                last = text
            else:
                last = ""
            time.sleep(
                min(self.poll_seconds, max(0.0, self._reply_time_left(deadline, deadline_utc)))
            )
        raise ChatGPTWebError(
            "Timed out waiting for the ChatGPT reply - the model may still be "
            "running the experiment; re-run to continue from the checkpoint"
        )
