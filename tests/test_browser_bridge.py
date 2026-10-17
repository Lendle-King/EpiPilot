"""EpiPilot Chrome extension bridge: hub routing and ChatGPT driver contract tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from epipilot.workspace.browser_bridge import ExtensionBridgeHub, ExtensionChatGPTDriver
from epipilot.workspace.chatgpt_web import ChatGPTWebError


class FakeHub:
    def __init__(self, respond: Any) -> None:
        self.respond = respond
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def request(self, action: str, timeout: float = 120.0, **payload: Any) -> dict[str, Any]:
        self.calls.append((action, payload))
        return self.respond(action, payload)


def test_hub_request_fails_closed_without_extension() -> None:
    hub = ExtensionBridgeHub()
    assert hub.connected is False
    with pytest.raises(ChatGPTWebError, match="not connected"):
        hub.request("tabs")
    hub.submit_result({"id": "404", "ok": True, "result": {}})  # unknown id is a no-op


def test_hub_queues_commands_and_resolves_results() -> None:
    hub = ExtensionBridgeHub()
    hub.heartbeat("epipilot-chrome-bridge")
    assert hub.connected is True

    outcome: dict[str, Any] = {}

    def worker() -> None:
        outcome["value"] = hub.request("tabs", match="chatgpt.com")

    thread = threading.Thread(target=worker)
    thread.start()
    command = None
    for _ in range(100):
        command = hub.next_command()
        if command:
            break
        time.sleep(0.01)
    assert command is not None
    assert command["action"] == "tabs"
    hub.submit_result({"id": command["id"], "ok": True, "result": {"tabs": []}})
    thread.join(timeout=2)
    assert outcome["value"] == {"tabs": []}


def test_inspect_fails_closed_without_extension_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from epipilot.workspace import api

    class DisconnectedHub:
        connected = False

        def __init__(self) -> None:
            self.heartbeat_calls = 0
            self.request_calls = 0

        def heartbeat(self, name: str = "") -> None:
            self.heartbeat_calls += 1

        def request(self, action: str, **payload: Any) -> dict[str, Any]:
            self.request_calls += 1
            raise AssertionError("a disconnected bridge must not enqueue commands")

    hub = DisconnectedHub()
    monkeypatch.setattr(api, "_extension_hub", hub)

    with pytest.raises(HTTPException) as raised:
        api.browser_bridge_inspect()

    assert raised.value.status_code == 503
    assert hub.heartbeat_calls == 0
    assert hub.request_calls == 0


def test_extension_driver_runs_the_full_chatgpt_sequence() -> None:
    reads = [
        {
            "composer": 'div.ProseMirror[contenteditable="true"]',
            "login": False,
            "stopVisible": False,
            "assistantText": "old answer from a previous turn",
        },
        {
            "composer": 'div.ProseMirror[contenteditable="true"]',
            "login": False,
            "stopVisible": False,
            "assistantText": "",
        },
        {
            "composer": 'div.ProseMirror[contenteditable="true"]',
            "login": False,
            "stopVisible": True,
            "assistantText": "partial",
        },
        {
            "composer": 'div.ProseMirror[contenteditable="true"]',
            "login": False,
            "stopVisible": False,
            "assistantText": "final answer",
        },
    ]

    def respond(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "open_tab":
            return {"tab_id": 7, "reused": True}
        if action == "read_state":
            return (
                reads.pop(0)
                if reads
                else {
                    "composer": 'div.ProseMirror[contenteditable="true"]',
                    "login": False,
                    "stopVisible": False,
                    "assistantText": "final answer",
                }
            )
        if action == "type":
            assert payload["text"].startswith("EpiPilot experiment route iteration")
            return {"typed": True, "selector": 'div.ProseMirror[contenteditable="true"]'}
        if action == "click":
            return {"clicked": True, "selector": 'button[data-testid="send-button"]'}
        raise AssertionError("unexpected action " + action)

    hub = FakeHub(respond)
    driver = ExtensionChatGPTDriver(hub=hub, timeout_seconds=5.0, poll_seconds=0.01)  # type: ignore[arg-type]
    answer = driver.submit("EpiPilot experiment route iteration for P.")
    # stale baseline and the user's own turn are never accepted as the reply
    assert answer == "final answer"
    actions = [action for action, _ in hub.calls]
    assert actions == [
        "open_tab",
        "read_state",
        "type",
        "click",
        "read_state",
        "read_state",
        "read_state",
        "read_state",
    ]


def test_extension_driver_reports_login_wall_and_typing_failures() -> None:
    def respond(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "open_tab":
            return {"tab_id": 7}
        if action == "read_state":
            return {"composer": None, "login": True, "stopVisible": False, "lastAssistantText": ""}
        raise AssertionError("unexpected action " + action)

    driver = ExtensionChatGPTDriver(
        hub=FakeHub(respond),
        timeout_seconds=0.2,
        poll_seconds=0.01,  # type: ignore[arg-type]
    )
    with pytest.raises(ChatGPTWebError, match="log in once"):
        driver.submit("pack")

    def typed_fail(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "open_tab": {"tab_id": 7},
            "read_state": {"composer": "#prompt-textarea", "assistantText": ""},
            "type": {"typed": False},
        }[action]

    driver = ExtensionChatGPTDriver(
        hub=FakeHub(typed_fail),
        timeout_seconds=1.0,
        poll_seconds=0.01,  # type: ignore[arg-type]
    )
    with pytest.raises(ChatGPTWebError, match="composer"):
        driver.submit("pack")


def test_extension_driver_rejects_stale_protocol_before_sending() -> None:
    def respond(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "open_tab":
            return {"tab_id": 7}
        if action == "read_state":
            return {"composer": "#prompt-textarea", "lastAssistantText": "old reply"}
        raise AssertionError("must not send a prompt through an incompatible extension")

    driver = ExtensionChatGPTDriver(hub=FakeHub(respond))  # type: ignore[arg-type]
    with pytest.raises(ChatGPTWebError, match="reload"):
        driver.submit("pack")


def test_default_wait_accepts_reply_after_ninety_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 0.0

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    monkeypatch.setattr(time, "monotonic", lambda: now)
    monkeypatch.setattr(time, "sleep", sleep)

    def respond(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "open_tab":
            return {"tab_id": 7}
        if action == "read_state":
            return {
                "composer": "#prompt-textarea",
                "assistantText": "final" if now >= 6000 else "old",
                "stopVisible": now < 6000,
            }
        return {"typed": True, "clicked": True}

    driver = ExtensionChatGPTDriver(hub=FakeHub(respond), poll_seconds=1000)  # type: ignore[arg-type]
    assert driver.submit("pack") == "final"


def test_wait_existing_reply_never_types_or_clicks() -> None:
    binding = {
        "conversationUrl": "https://chatgpt.com/c/synthetic",
        "userTurnCount": 1,
        "lastUserRouteRunId": "route-123",
    }
    states = iter(
        [
            {
                **binding,
                "assistantText": "pending route-123",
                "lastAssistantText": "route-123",
                "stopVisible": True,
            },
            {**binding, "assistantText": "final reply", "stopVisible": False},
            {**binding, "assistantText": "final reply", "stopVisible": False},
        ]
    )

    def respond(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert action == "read_state"
        assert payload["tab_id"] == 7
        return next(states)

    driver = ExtensionChatGPTDriver(hub=FakeHub(respond), poll_seconds=0.001)  # type: ignore[arg-type]
    assert driver.wait_existing_reply(7, "route-123") == "final reply"


def test_wait_existing_reply_rejects_unrelated_tab() -> None:
    driver = ExtensionChatGPTDriver(
        hub=FakeHub(lambda action, payload: {"assistantText": "other route"})
    )  # type: ignore[arg-type]
    with pytest.raises(ChatGPTWebError, match="route"):
        driver.wait_existing_reply(7, "route-123")


@pytest.mark.parametrize(
    "changed",
    [
        {"conversationUrl": "https://chatgpt.com/c/other"},
        {"userTurnCount": 2},
        {"lastUserRouteRunId": "other-route"},
    ],
)
def test_recovery_rejects_changed_conversation_binding(changed: dict[str, Any]) -> None:
    initial = {
        "conversationUrl": "https://chatgpt.com/c/synthetic",
        "userTurnCount": 1,
        "lastUserRouteRunId": "route-123",
        "assistantText": "pending",
        "lastAssistantText": "route-123",
        "stopVisible": True,
    }
    changed_state = {
        **initial,
        **changed,
        "assistantText": "unrelated valid review",
        "stopVisible": False,
    }
    states = iter([initial, changed_state, changed_state])
    driver = ExtensionChatGPTDriver(
        hub=FakeHub(lambda action, payload: next(states)), poll_seconds=0.001
    )  # type: ignore[arg-type]
    with pytest.raises(ChatGPTWebError, match="binding"):
        driver.wait_existing_reply(7, "route-123")


def test_recovery_accepts_completion_when_only_stop_indicator_changes() -> None:
    initial = {
        "conversationUrl": "https://chatgpt.com/c/synthetic",
        "userTurnCount": 1,
        "lastUserRouteRunId": "route-123",
        "assistantText": "final reply",
        "lastAssistantText": "route-123",
        "stopVisible": True,
    }
    states = iter([initial, {**initial, "stopVisible": False}, {**initial, "stopVisible": False}])
    driver = ExtensionChatGPTDriver(
        hub=FakeHub(lambda action, payload: next(states)), poll_seconds=0.001
    )  # type: ignore[arg-type]
    assert driver.wait_existing_reply(7, "route-123") == "final reply"


def test_recovery_includes_initial_read_in_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 0.0
    monkeypatch.setattr(time, "monotonic", lambda: now)

    def respond(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal now
        if now == 0:
            now = 3
        return {
            "conversationUrl": "https://chatgpt.com/c/synthetic",
            "userTurnCount": 1,
            "lastUserRouteRunId": "route-123",
            "assistantText": "final reply",
            "lastAssistantText": "route-123",
            "stopVisible": False,
        }

    driver = ExtensionChatGPTDriver(hub=FakeHub(respond), timeout_seconds=2)  # type: ignore[arg-type]
    with pytest.raises(ChatGPTWebError, match="Timed out"):
        driver.wait_existing_reply(7, "route-123")


@pytest.mark.parametrize("complete", [True, False])
def test_extension_binding_tracks_latest_user_route(complete: bool) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the actual Chrome extension helper")
    script = r"""
const fs = require('fs'), vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const helper = 'function injectedReadState' + source.split('function injectedReadState')[1]
  .split('// --- command dispatch')[0];
const lines = ['你说\uFF1A', 'EpiPilot route', 'ROUTE:', '{"run_id":"route-old"}',
  'ChatGPT 说\uFF1A', '{"verdict":"blocked"}', 'You said:', 'EpiPilot route', 'ROUTE:',
  '{"run_id":"route-new"}'];
if (process.argv[2] === 'complete') lines.push('ChatGPT said:', 'final reply');
const transcript = lines.join('\n');
const document = {body: {innerText: transcript}, querySelectorAll: () => [],
  querySelector: s => s === 'main' ? {innerText: transcript} : null};
const result = vm.runInNewContext(helper + ';injectedReadState({})',
  {document, location: {href:'https://chatgpt.com/c/synthetic'}});
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        [
            node,
            "-e",
            script,
            str(Path(__file__).parents[1] / "extension/background.js"),
            "complete" if complete else "pending",
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    state = json.loads(result.stdout)
    assert state["conversationUrl"] == "https://chatgpt.com/c/synthetic"
    assert state["userTurnCount"] == 2
    assert state["lastUserRouteRunId"] == "route-new"
    assert state["assistantText"] == ("final reply" if complete else "")


def test_recovery_obeys_original_utc_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from epipilot.workspace import browser_bridge

    now = datetime.now(UTC)
    deadline = now + timedelta(seconds=2)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now

    def respond(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal now
        now = deadline + timedelta(seconds=1)
        return {
            "conversationUrl": "https://chatgpt.com/c/synthetic",
            "userTurnCount": 1,
            "lastUserRouteRunId": "route-123",
            "assistantText": "final",
            "stopVisible": False,
        }

    monkeypatch.setattr(browser_bridge, "datetime", Clock, raising=False)
    driver = ExtensionChatGPTDriver(hub=FakeHub(respond))  # type: ignore[arg-type]
    with pytest.raises(ChatGPTWebError, match="Timed out"):
        driver.wait_existing_reply(7, "route-123", deadline_utc=deadline)
