from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from epipilot.executors.agent_browser import (
    AgentBrowserLauncher,
    BrowserLaunchError,
    session_for_task,
)
from epipilot.executors.chatgpt_handoff import (
    ChatGPTWebHandoffStore,
    DelegationReceipt,
    HandoffState,
    ResearchHandoff,
)


def _spec(task_id: str = "gui-1") -> ResearchHandoff:
    return ResearchHandoff(
        task_id=task_id,
        objective="Measure anonymization effect",
        context="Seed 3; do not alter benchmark evaluator",
        github_repos=("https://github.com/Lendle-King/EpiPilot",),
        notion_pages=("https://www.notion.so/example123",),
    )


def test_prompt_only_delegates_github_notion_to_chatgpt() -> None:
    text = _spec().prompt()
    assert "GitHub repositories" in text and "Notion destinations" in text
    assert "UNVERIFIED" in text and "smallest informative experiment" in text
    assert "https://github.com/Lendle-King/EpiPilot" in text


def test_rejects_unsafe_inputs() -> None:
    with pytest.raises(ValueError, match="safe"):
        _spec("../escape")
    with pytest.raises(ValueError, match="GitHub"):
        ResearchHandoff("id", "goal", "context", ("https://evil.com/path",), (
            "https://notion.so/abc",))
    with pytest.raises(ValueError, match="HTTPS"):
        ResearchHandoff("id", "goal", "context", ("https://github.com/a/b",), (
            "http://notion.so/abc",))


def test_handoff_state_and_receipt_are_explicitly_unverified(tmp_path: Path) -> None:
    store = ChatGPTWebHandoffStore(tmp_path / "private")
    prompt = store.prepare(_spec())
    assert prompt.exists()
    assert store.status("gui-1")["state"] == HandoffState.PREPARED
    assert store.prepare(_spec()) == prompt
    with pytest.raises(ValueError, match="differs"):
        store.prepare(ResearchHandoff("gui-1", "different", "ctx", _spec().github_repos,
                                     _spec().notion_pages))
    with pytest.raises(ValueError, match="opened"):
        store.report("gui-1", b'{"summary":"pretend done"}')
    store.opened("gui-1")
    store.report("gui-1", json.dumps({
        "summary": "Finished as claimed",
        "github_evidence": ["https://github.com/Lendle-King/EpiPilot/pull/123"],
        "notion_evidence": ["https://www.notion.so/example123"],
    }).encode())
    state = store.status("gui-1")
    assert state["state"] == HandoffState.RESULT_REPORTED
    assert "verified" not in state and "passed" not in state
    assert state["session"] == session_for_task("gui-1")
    assert json.loads((prompt.parent / "receipt.json").read_text())["summary"] == (
        "Finished as claimed")
    with pytest.raises(ValueError, match="reopen"):
        store.opened("gui-1")
    with pytest.raises(ValueError, match="opened"):
        store.report("gui-1", b'{"summary":"again"}')


def test_receipt_cannot_smuggle_external_domains_or_unknown_fields() -> None:
    with pytest.raises(ValueError, match="GitHub"):
        DelegationReceipt.parse(b'{"summary":"x","github_evidence":["https://evil.org/x"]}')
    with pytest.raises(ValueError, match="unexpected"):
        DelegationReceipt.parse(b'{"summary":"x","verified":true}')
    with pytest.raises(ValueError, match="list"):
        DelegationReceipt.parse(b'{"summary":"x","limitations":"none"}')


def test_missing_unknown_handoff_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ChatGPTWebHandoffStore(tmp_path).status("not-found")


@pytest.mark.asyncio
async def test_browser_uses_visible_restored_session_and_fixed_site() -> None:
    browser = AgentBrowserLauncher()
    with patch.object(AgentBrowserLauncher, "_run", new_callable=AsyncMock) as command:
        await browser.open("epipilot-test")
        command.assert_awaited_once_with(
            "--session", "epipilot-test", "--restore", "--headed", "open",
            "https://chatgpt.com/",
        )
        command.reset_mock()
        await browser.close("epipilot-test")
        command.assert_awaited_once_with("--session", "epipilot-test", "--restore", "close")
    with pytest.raises(ValueError, match="session"):
        await browser.open("evil; echo pwn")


@pytest.mark.asyncio
async def test_missing_cli_fails_with_no_sensitive_stderr() -> None:
    browser = AgentBrowserLauncher(binary="not-a-real-browser-cli-20989")
    with pytest.raises(BrowserLaunchError, match="unavailable"):
        await browser.open("valid")


def test_session_is_stable_and_not_plaintext_identifier() -> None:
    assert session_for_task("task-with-secret") == session_for_task("task-with-secret")
    assert "secret" not in session_for_task("task-with-secret")
    assert session_for_task("task-1") != session_for_task("task-2")
