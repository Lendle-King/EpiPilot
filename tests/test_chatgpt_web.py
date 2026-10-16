"""ChatGPT Web adapter: JSON extraction, pack building and iteration import tests."""

from __future__ import annotations

from typing import Any

import pytest

from epipilot.workspace.chatgpt_web import (
    ChatGPTWebError,
    ChatGPTWebSettings,
    extract_route_review_json,
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


def test_browser_settings_require_https_and_positive_timeout(tmp_path: Any) -> None:
    with pytest.raises(ValueError, match="https"):
        ChatGPTWebSettings(profile_dir=tmp_path, url="http://chatgpt.com/")
    with pytest.raises(ValueError, match="timeout"):
        ChatGPTWebSettings(profile_dir=tmp_path, timeout_seconds=0)
    settings = ChatGPTWebSettings(profile_dir=tmp_path)
    assert settings.url == "https://chatgpt.com/"
    assert settings.browser_channel == "chrome"
    assert settings.browser_executable is None
    native = ChatGPTWebSettings(profile_dir=tmp_path, browser_executable="/usr/bin/chrome")
    assert native.browser_executable == "/usr/bin/chrome"
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
