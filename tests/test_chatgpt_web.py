"""ChatGPT Web adapter: JSON extraction, pack building and iteration import tests."""

from __future__ import annotations

from typing import Any

import pytest

from epipilot.workspace.chatgpt_web import (
    _LOGIN_ROLES,
    DEFAULT_CDP_ENDPOINT,
    ChatGPTWebError,
    ChatGPTWebSettings,
    extract_route_review_json,
    resolve_attach_cdp_url,
)
from epipilot.workspace.experiment_routes import ExperimentRoutePlanner, build_iteration_pack
from test_experiment_routes import FakeRouteModel, _route_payload, _seed_claims
from test_research_onboarding_agent import _setup

_REVIEW = (
    '{"verdict": "feasible", "evaluation_zh": "路线可行且与 claims 一致。", '
    '"results_digest_zh": "对照回测完成: 验证期 Calmar 提升 0.05。", '
    '"recommended_next_zh": "扩大邻域检验。", "evidence_refs": ["out/run_1"]}'
)


def test_extracts_review_json_from_fenced_block_with_nested_braces() -> None:
    text = "Analysis done.\n\n```json\n" + _REVIEW + "\n```\nEnd."
    assert extract_route_review_json(text) == _REVIEW


def test_extracts_review_json_from_prose_with_competing_objects() -> None:
    text = 'Config {"model": "x"} then the result: ' + _REVIEW + " done."
    assert extract_route_review_json(text) == _REVIEW


def test_extraction_fails_closed_without_review_json() -> None:
    with pytest.raises(ChatGPTWebError, match="No route-review JSON"):
        extract_route_review_json('{"model": "x"} only config here')
    with pytest.raises(ChatGPTWebError):
        extract_route_review_json("no json at all")


def test_iteration_pack_carries_route_and_contract() -> None:
    pack = build_iteration_pack({"proposals": []}, "QUANT_ETF")
    assert "QUANT_ETF" in pack
    assert '"verdict"' in pack
    assert "ROUTE:" in pack
    assert "Do not invoke GitHub/Notion remote actions" in pack


def test_browser_settings_require_https_and_positive_timeout(tmp_path: Any) -> None:
    with pytest.raises(ValueError, match="https"):
        ChatGPTWebSettings(profile_dir=tmp_path, url="http://chatgpt.com/")
    with pytest.raises(ValueError, match="timeout"):
        ChatGPTWebSettings(profile_dir=tmp_path, timeout_seconds=0)
    settings = ChatGPTWebSettings(profile_dir=tmp_path)
    assert settings.url == "https://chatgpt.com/"
    assert settings.timeout_seconds == 7200.0
    assert settings.browser_channel == "chrome"
    assert settings.browser_executable is None
    native = ChatGPTWebSettings(profile_dir=tmp_path, browser_executable="/usr/bin/chrome")
    assert native.browser_executable == "/usr/bin/chrome"
    assert ChatGPTWebSettings(profile_dir=tmp_path).attach_cdp_url is None
    attached = ChatGPTWebSettings(profile_dir=tmp_path, attach_cdp_url="http://127.0.0.1:9222")
    assert attached.attach_cdp_url == "http://127.0.0.1:9222"
    with pytest.raises(ValueError, match="local http endpoint"):
        ChatGPTWebSettings(profile_dir=tmp_path, attach_cdp_url="https://example.com:9222")
    with pytest.raises(ValueError, match="local http endpoint"):
        ChatGPTWebSettings(profile_dir=tmp_path, attach_cdp_url="http://10.0.0.5:9222")
    assert settings.login_grace_seconds == 600.0
    with pytest.raises(ValueError, match="login grace"):
        ChatGPTWebSettings(profile_dir=tmp_path, login_grace_seconds=-1)


class FakeDriver:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompt = ""

    def submit(self, prompt: str) -> str:
        self.prompt = prompt
        return self.reply


def test_run_chatgpt_iteration_imports_review_through_validation(tmp_path: Any) -> None:
    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    planner = ExperimentRoutePlanner(store)
    planner.generate(project_id, FakeRouteModel(_route_payload(claim_id)), "fake")
    driver = FakeDriver("Done.\n```json\n" + _REVIEW + "\n```")

    report = planner.run_chatgpt_iteration(project_id, driver, "QUANT_ETF")
    assert report["review"]["verdict"] == "feasible"
    assert "QUANT_ETF" in driver.prompt
    assert "ROUTE:" in driver.prompt


def test_run_chatgpt_iteration_requires_a_route_and_valid_json(tmp_path: Any) -> None:
    store, project_id = _setup(tmp_path)
    planner = ExperimentRoutePlanner(store)
    with pytest.raises(ValueError, match="No experiment route"):
        planner.run_chatgpt_iteration(project_id, FakeDriver("x"), "P")

    claim_id = _seed_claims(store, project_id)
    planner.generate(project_id, FakeRouteModel(_route_payload(claim_id)), "fake")
    with pytest.raises(ChatGPTWebError):
        planner.run_chatgpt_iteration(project_id, FakeDriver("no json here"), "P")


def test_cdp_attach_is_the_default_when_local_endpoint_answers() -> None:
    def live(url: str) -> bool:
        return url == DEFAULT_CDP_ENDPOINT

    def dead(url: str) -> bool:
        return False

    assert resolve_attach_cdp_url(None, live) == DEFAULT_CDP_ENDPOINT
    assert resolve_attach_cdp_url(None, dead) is None
    assert resolve_attach_cdp_url("http://localhost:9333", dead) == "http://localhost:9333"
    assert resolve_attach_cdp_url("off", live) is None
    assert resolve_attach_cdp_url("none", live) is None
    assert resolve_attach_cdp_url("", live) is None


class _FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _FakePage:
    def __init__(self, login_buttons: int) -> None:
        self._login_buttons = login_buttons

    def get_by_role(self, role: str, name: object = None) -> _FakeLocator:
        return _FakeLocator(self._login_buttons)


def test_missing_composer_distinguishes_login_wall_from_ui_change() -> None:
    from epipilot.workspace.chatgpt_web import PlaywrightChatGPTDriver

    with pytest.raises(ChatGPTWebError, match="not logged in"):
        PlaywrightChatGPTDriver._raise_composer_missing(_FakePage(1))
    with pytest.raises(ChatGPTWebError, match="composer not found"):
        PlaywrightChatGPTDriver._raise_composer_missing(_FakePage(0))


class _FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _FakePage:
    def __init__(self, login_wall: bool) -> None:
        self._login_wall = login_wall
        self.role_calls: list[object] = []

    def get_by_role(self, role: str, name: object = None) -> _FakeLocator:
        self.role_calls.append(name)
        return _FakeLocator(1 if self._login_wall else 0)


def test_composer_missing_triage_distinguishes_login_wall() -> None:
    from epipilot.workspace.chatgpt_web import PlaywrightChatGPTDriver

    login_page = _FakePage(login_wall=True)
    with pytest.raises(ChatGPTWebError, match="not logged in"):
        PlaywrightChatGPTDriver._raise_composer_missing(login_page)  # type: ignore[arg-type]
    # accessible-name matching must use a pattern/string, never a tuple
    assert login_page.role_calls == [_LOGIN_ROLES]

    blank_page = _FakePage(login_wall=False)
    with pytest.raises(ChatGPTWebError, match="composer not found"):
        PlaywrightChatGPTDriver._raise_composer_missing(blank_page)  # type: ignore[arg-type]


def test_composer_missing_survives_locator_failures() -> None:
    from epipilot.workspace.chatgpt_web import PlaywrightChatGPTDriver

    class Broken:
        def get_by_role(self, role: str, name: object = None) -> object:
            raise TypeError("bad locator")

    with pytest.raises(ChatGPTWebError, match="composer not found"):
        PlaywrightChatGPTDriver._raise_composer_missing(Broken())  # type: ignore[arg-type]
