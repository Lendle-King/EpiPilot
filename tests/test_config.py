from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from epipilot.config import (
    ConfigError,
    ModelConfig,
    ReasoningEffort,
    load_default_model_config,
)


def test_default_model_config_matches_declared_profile() -> None:
    config = load_default_model_config({"EPIPILOT_API_KEY": "test-key"}, env_file=None)

    assert config.provider == "Codexforme"
    assert config.base_url == "https://blackaicoding.com/v1"
    assert config.model == "gpt-6.1-sol"
    assert config.reasoning_effort is ReasoningEffort.XHIGH
    assert config.chat_completions_url == "https://blackaicoding.com/v1/chat/completions"


def test_missing_api_key_fails_closed() -> None:
    with pytest.raises(ConfigError, match="EPIPILOT_API_KEY"):
        load_default_model_config({}, env_file=None)


def test_blank_api_key_fails_closed() -> None:
    with pytest.raises(ConfigError, match="EPIPILOT_API_KEY"):
        load_default_model_config({"EPIPILOT_API_KEY": "   "}, env_file=None)


def test_environment_overrides_defaults() -> None:
    config = load_default_model_config(
        {
            "EPIPILOT_API_KEY": "test-key",
            "EPIPILOT_PROVIDER": "OtherProvider",
            "EPIPILOT_BASE_URL": "https://example.com/v1",
            "EPIPILOT_MODEL": "other-model",
            "EPIPILOT_REASONING_EFFORT": "high",
        },
        env_file=None,
    )

    assert config.provider == "OtherProvider"
    assert config.base_url == "https://example.com/v1"
    assert config.model == "other-model"
    assert config.reasoning_effort is ReasoningEffort.HIGH


def test_unknown_reasoning_effort_fails_closed() -> None:
    with pytest.raises(ConfigError, match="EPIPILOT_REASONING_EFFORT must be one of"):
        load_default_model_config(
            {"EPIPILOT_API_KEY": "test-key", "EPIPILOT_REASONING_EFFORT": "ultra"},
            env_file=None,
        )


def test_invalid_base_url_fails_closed() -> None:
    with pytest.raises(ConfigError, match="base_url"):
        load_default_model_config(
            {"EPIPILOT_API_KEY": "test-key", "EPIPILOT_BASE_URL": "not-a-url"},
            env_file=None,
        )
    with pytest.raises(ConfigError, match="base_url"):
        load_default_model_config(
            {"EPIPILOT_API_KEY": "test-key", "EPIPILOT_BASE_URL": "ftp://example.com/v1"},
            env_file=None,
        )


def test_blank_provider_and_model_fail_closed() -> None:
    with pytest.raises(ConfigError, match="provider must not be empty"):
        load_default_model_config({"EPIPILOT_API_KEY": "test-key", "EPIPILOT_PROVIDER": "  "})
    with pytest.raises(ConfigError, match="model must not be empty"):
        load_default_model_config({"EPIPILOT_API_KEY": "test-key", "EPIPILOT_MODEL": ""})


def test_api_key_is_hidden_from_repr() -> None:
    config = ModelConfig(api_key="super-secret-key")

    assert "super-secret-key" not in repr(config)
    assert "super-secret-key" not in str(config)


def test_config_is_immutable() -> None:
    config = ModelConfig(api_key="test-key")

    with pytest.raises(FrozenInstanceError):
        config.model = "other-model"  # type: ignore[misc]


def test_blank_api_key_rejected_on_direct_construction() -> None:
    with pytest.raises(ConfigError, match="api key must not be empty"):
        ModelConfig(api_key="  ")


def test_reasoning_effort_must_be_enum_member() -> None:
    with pytest.raises(ConfigError, match="ReasoningEffort"):
        ModelConfig(api_key="test-key", reasoning_effort="xhigh")  # type: ignore[arg-type]


def test_env_file_is_read_and_environment_wins(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# local configuration\n\nEPIPILOT_API_KEY=file-key\nEPIPILOT_MODEL="file-model"\n',
        encoding="utf-8",
    )

    config = load_default_model_config({"EPIPILOT_API_KEY": "env-key"}, env_file=env_file)

    assert config.api_key == "env-key"
    assert config.model == "file-model"


def test_malformed_env_file_fails_closed(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("this line has no assignment\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="expected KEY=VALUE"):
        load_default_model_config({"EPIPILOT_API_KEY": "test-key"}, env_file=env_file)


def test_missing_env_file_is_skipped(tmp_path: Path) -> None:
    config = load_default_model_config(
        {"EPIPILOT_API_KEY": "test-key"},
        env_file=tmp_path / "absent.env",
    )

    assert config.model == "gpt-6.1-sol"


def test_chat_completions_url_normalizes_trailing_slash() -> None:
    config = ModelConfig(api_key="test-key", base_url="https://example.com/v1/")

    assert config.chat_completions_url == "https://example.com/v1/chat/completions"


def test_base_url_with_whitespace_fails_closed() -> None:
    with pytest.raises(ConfigError, match="whitespace"):
        ModelConfig(api_key="test-key", base_url=" https://example.com/v1")
