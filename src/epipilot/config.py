"""Default model profile resolved from environment variables and an optional ``.env`` file.

The API key is treated as a credential: it is never included in ``repr`` and must come
from the process environment or a gitignored local ``.env`` file, never from source.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

API_KEY_ENV_VAR = "EPIPILOT_API_KEY"
PROVIDER_ENV_VAR = "EPIPILOT_PROVIDER"
BASE_URL_ENV_VAR = "EPIPILOT_BASE_URL"
MODEL_ENV_VAR = "EPIPILOT_MODEL"
REASONING_EFFORT_ENV_VAR = "EPIPILOT_REASONING_EFFORT"

DEFAULT_PROVIDER = "Codexforme"
DEFAULT_BASE_URL = "https://blackaicoding.com/v1"
DEFAULT_MODEL = "gpt-6.1-sol"


class ConfigError(ValueError):
    """Raised when model configuration is missing, malformed, or ambiguous."""


class ReasoningEffort(StrEnum):
    """Reasoning effort levels accepted by the default model profile."""

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


DEFAULT_REASONING_EFFORT = ReasoningEffort.XHIGH


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Immutable default model profile for LLM-backed framework components."""

    api_key: str = field(repr=False)
    provider: str = DEFAULT_PROVIDER
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ConfigError("api key must not be empty")
        if not self.provider.strip():
            raise ConfigError("provider must not be empty")
        if not self.model.strip():
            raise ConfigError("model must not be empty")
        if not isinstance(self.reasoning_effort, ReasoningEffort):
            raise ConfigError(
                f"reasoning effort must be a ReasoningEffort member: {self.reasoning_effort!r}"
            )
        if any(character.isspace() for character in self.base_url):
            raise ConfigError("base_url must not contain whitespace")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigError(f"base_url must be an absolute http(s) URL: {self.base_url!r}")

    @property
    def chat_completions_url(self) -> str:
        """Absolute chat-completions endpoint for this profile."""
        return f"{self.base_url.rstrip('/')}/chat/completions"


def load_default_model_config(
    environ: Mapping[str, str] | None = None,
    *,
    env_file: Path | None = Path(".env"),
) -> ModelConfig:
    """Build the default :class:`ModelConfig` from environment inputs.

    Precedence is process/argument environment over ``env_file``; a missing ``env_file``
    is skipped, a malformed one fails closed. When ``environ`` is ``None`` the process
    environment is used; passing an explicit mapping replaces it entirely.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ

    env: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        env.update(_parse_env_file(env_file))
    env.update(source)

    api_key = env.get(API_KEY_ENV_VAR, "").strip()
    if not api_key:
        raise ConfigError(f"{API_KEY_ENV_VAR} is required but not set")

    return ModelConfig(
        api_key=api_key,
        provider=env.get(PROVIDER_ENV_VAR, DEFAULT_PROVIDER),
        base_url=env.get(BASE_URL_ENV_VAR, DEFAULT_BASE_URL),
        model=env.get(MODEL_ENV_VAR, DEFAULT_MODEL),
        reasoning_effort=_parse_reasoning_effort(
            env.get(REASONING_EFFORT_ENV_VAR, DEFAULT_REASONING_EFFORT)
        ),
    )


def _parse_reasoning_effort(value: str) -> ReasoningEffort:
    try:
        return ReasoningEffort(value)
    except ValueError as error:
        supported = ", ".join(member.value for member in ReasoningEffort)
        raise ConfigError(f"{REASONING_EFFORT_ENV_VAR} must be one of: {supported}") from error


def _parse_env_file(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise ConfigError(f"{path}:{line_number}: expected KEY=VALUE entry")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        entries[key.strip()] = value
    return entries
