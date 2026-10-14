"""Third-party research API contract tests: HTTPS, secrets and structured results."""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from epipilot.workspace.remote_model import (
    ThirdPartyChatModel,
    _endpoint,
    _strict_schema,
    model_configuration,
)
from epipilot.workspace.research_agent import ClaimBatch


class FakeHTTPResponse(io.BytesIO):
    def __init__(self, payload: bytes, url: str) -> None:
        super().__init__(payload)
        self.url = url

    def geturl(self) -> str:
        return self.url


class FakeOpener:
    def __init__(self, completion: dict[str, Any]) -> None:
        self.completion = completion
        self.requests: list[Any] = []

    def open(self, request: Any, timeout: float) -> FakeHTTPResponse:
        self.requests.append(request)
        return FakeHTTPResponse(
            json.dumps(self.completion).encode(), request.full_url
        )


def _completion(content: str, reason: str = "stop") -> dict[str, object]:
    return {
        "choices": [
            {"finish_reason": reason, "message": {"content": content}}
        ]
    }


@pytest.mark.parametrize(
    "base",
    [
        "http://api.example.com/v1",
        "https://user:pw@api.example.com/v1",
        "https://127.0.0.1/v1",
        "https://10.1.2.3/v1",
        "https://localhost/v1",
        "https://api.example.com/v1?key=secret",
        "https://api.example.com/v1/chat/completions",
    ],
)
def test_rejects_invalid_or_unsafe_external_api_endpoints(base: str) -> None:
    with pytest.raises(ValueError):
        _endpoint(base)


def test_openai_compatible_chat_payload_and_server_only_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeOpener(_completion('{"claims": []}'))
    monkeypatch.setattr("epipilot.workspace.remote_model.urllib.request.build_opener",
                        lambda handler: fake)
    model = ThirdPartyChatModel(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-secret-never-log",
        model="openai/gpt-4o",
    )
    result = model.extract("Analyze this source excerpt")
    assert result == ClaimBatch(claims=[])
    request = fake.requests[0]
    assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer test-secret-never-log"
    body = json.loads(request.data)
    assert body["model"] == "openai/gpt-4o"
    assert body["response_format"]["type"] == "json_schema"
    output_schema = body["response_format"]["json_schema"]["schema"]
    assert body["response_format"]["json_schema"]["strict"] is True
    nested = output_schema["$defs"]["GroundedClaim"]
    assert set(nested["required"]) == set(nested["properties"])
    assert "minLength" not in nested["properties"]["proposition"]
    assert nested["additionalProperties"] is False
    assert "options" not in body
    assert "format" not in body


def test_json_object_fallback_and_pydantic_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeOpener(_completion('{"claims": []}'))
    monkeypatch.setattr(
        "epipilot.workspace.remote_model.urllib.request.build_opener",
        lambda handler: fake,
    )
    model = ThirdPartyChatModel(
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="deepseek-chat",
        response_format="json_object",
        max_tokens=2048,
    )
    assert model.extract("Analyze").claims == []
    payload = json.loads(fake.requests[0].data)
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 2048
    assert "schema" in payload["messages"][0]["content"].lower()

    fake.completion = _completion('{"claims": [{"not_a_field":"abc"}]}')
    with pytest.raises(ValueError, match="local schema validation"):
        model.extract("Analyze")


def test_configuration_only_exposes_nonsecret_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EPIPILOT_RESEARCH_API_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setenv("EPIPILOT_RESEARCH_API_KEY", "private-token-very-secret")
    monkeypatch.setenv("EPIPILOT_RESEARCH_API_MODEL", "Pro/example/Model")
    monkeypatch.setenv("EPIPILOT_RESEARCH_API_FORMAT", "json_object")
    config = model_configuration()
    assert config["configured"] is True
    assert config["provider_host"] == "api.siliconflow.cn"
    assert config["response_format"] == "json_object"
    assert "private-token-very-secret" not in str(config)
    assert "api_key" not in config
    model = ThirdPartyChatModel.from_environment()
    assert model.model == "Pro/example/Model"
    assert model.response_format == "json_object"
    assert ThirdPartyChatModel.from_environment("Other/model").model == "Other/model"


def test_missing_or_invalid_configuration_fails_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "EPIPILOT_RESEARCH_API_BASE_URL",
        "EPIPILOT_RESEARCH_API_KEY",
        "EPIPILOT_RESEARCH_API_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError):
        ThirdPartyChatModel.from_environment()
    monkeypatch.setenv("EPIPILOT_RESEARCH_API_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("EPIPILOT_RESEARCH_API_KEY", "not-empty")
    monkeypatch.setenv("EPIPILOT_RESEARCH_API_MODEL", "example")
    monkeypatch.setenv("EPIPILOT_RESEARCH_API_FORMAT", "invalid")
    with pytest.raises(ValueError, match="response format"):
        ThirdPartyChatModel.from_environment()


def test_http_failure_and_token_limit_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingOpener:
        def open(self, request: Any, timeout: float) -> None:
            raise urllib.error.HTTPError(
                request.full_url, 401, "sensitive provider response", {}, None
            )
    monkeypatch.setattr(
        "epipilot.workspace.remote_model.urllib.request.build_opener",
        lambda handler: FailingOpener(),
    )
    model = ThirdPartyChatModel("https://api.example.com/v1", "private-token", "model")
    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        model.extract("Test")
    assert "private-token" not in str(error.value)
    assert "sensitive provider response" not in str(error.value)

    fake = FakeOpener(_completion('{"claims":[]}', reason="length"))
    monkeypatch.setattr(
        "epipilot.workspace.remote_model.urllib.request.build_opener",
        lambda handler: fake,
    )
    with pytest.raises(ValueError, match="truncated"):
        model.extract("Test")


def test_schema_conversion_keeps_safety_in_local_pydantic() -> None:
    schema = _strict_schema(ClaimBatch.model_json_schema())
    assert "maxItems" not in schema["properties"]["claims"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"claims"}


def test_http_configuration_endpoint_does_not_expose_key(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from epipilot.workspace.api import app

    monkeypatch.setenv("EPIPILOT_RESEARCH_API_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("EPIPILOT_RESEARCH_API_KEY", "secret-should-not-appear")
    monkeypatch.setenv("EPIPILOT_RESEARCH_API_MODEL", "anthropic/claude-sonnet-4")
    monkeypatch.setenv("EPIPILOT_WORKSPACE_DB", str(tmp_path / "store.sqlite3"))
    res = TestClient(app).get("/api/research-model/config")
    assert res.status_code == 200
    assert res.json()["configured"] is True
    assert "secret-should-not-appear" not in res.text
