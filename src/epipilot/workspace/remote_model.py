"""Provider-neutral, HTTPS-only Chat Completions client for research intake.

Configuration is server-side only. No API keys, prompts or provider responses are
logged to EpiPilot's staging events; only validated claims are persisted.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import ValidationError

from epipilot.workspace.research_agent import ClaimBatch, GUIDANCE

ResponseFormat = Literal["json_schema", "json_object"]
_MAX_RESPONSE_BYTES = 350_000


def _endpoint(base_url: str) -> tuple[str, str]:
    """Validate an externally configured HTTPS base before adding the API path."""
    parsed = urllib.parse.urlsplit(base_url)
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or host.lower() in {"localhost", "localhost.localdomain"}
        or host.lower().endswith((".localhost", ".local", ".internal"))
        or any(ch.isspace() for ch in base_url)
    ):
        raise ValueError("Research API base URL must be a public HTTPS URL without credentials")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Research API base URL cannot use a non-public IP address")
    if parsed.path.rstrip("/").endswith("/chat/completions"):
        raise ValueError("Configure the API base URL, not the /chat/completions endpoint")
    try:
        _port = parsed.port
    except ValueError as error:
        raise ValueError("Invalid research API port") from error
    return base_url.rstrip("/") + "/chat/completions", host.lower()


def _strict_schema(value: Any) -> Any:
    """Convert Pydantic's validation schema into the provider strict JSON subset.

    The local Pydantic model retains minLength, pattern, maxItems, and other
    guards even though some remote strict-schema dialects reject those keywords.
    """
    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    skipped = {
        "default",
        "title",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "format",
    }
    result = {key: _strict_schema(child) for key, child in value.items() if key not in skipped}
    if result.get("type") == "object" or "properties" in result:
        result["additionalProperties"] = False
        properties = result.get("properties", {})
        result["required"] = list(properties)
    return result


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        """Never forward the bearer token to a redirect target."""
        return None


@dataclass(frozen=True, slots=True)
class ThirdPartyChatModel:
    """OpenAI-compatible HTTPS /chat/completions with local schema validation."""

    base_url: str
    api_key: str
    model: str
    response_format: ResponseFormat = "json_schema"
    timeout: float = 120.0
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        _endpoint(self.base_url)
        if not self.api_key or any(c in self.api_key for c in "\r\n"):
            raise ValueError("Research API key is missing or invalid")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,159}", self.model):
            raise ValueError("Invalid research API model identifier")
        if self.response_format not in {"json_schema", "json_object"}:
            raise ValueError("Unsupported research API response format")
        if not 1 <= self.timeout <= 180:
            raise ValueError("Research API timeout must be within 1–180 seconds")
        if self.max_tokens is not None and not 1 <= self.max_tokens <= 16000:
            raise ValueError("Research API max_tokens must be within 1–16000")

    @classmethod
    def from_environment(cls, model_override: str | None = None) -> ThirdPartyChatModel:
        base_url = os.environ.get("EPIPILOT_RESEARCH_API_BASE_URL", "")
        key = os.environ.get("EPIPILOT_RESEARCH_API_KEY", "")
        model = model_override or os.environ.get("EPIPILOT_RESEARCH_API_MODEL", "")
        fmt = os.environ.get("EPIPILOT_RESEARCH_API_FORMAT", "json_schema")
        timeout = float(os.environ.get("EPIPILOT_RESEARCH_API_TIMEOUT", "120"))
        token_setting = os.environ.get("EPIPILOT_RESEARCH_API_MAX_TOKENS")
        tokens = int(token_setting) if token_setting else None
        if fmt not in {"json_schema", "json_object"}:
            raise ValueError("Unsupported research API response format")
        return cls(
            base_url=base_url,
            api_key=key,
            model=model,
            response_format=cast(ResponseFormat, fmt),
            timeout=timeout,
            max_tokens=tokens,
        )

    def extract(self, prompt: str) -> ClaimBatch:
        endpoint, _host = _endpoint(self.base_url)
        schema = ClaimBatch.model_json_schema()
        if self.response_format == "json_schema":
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "epipilot_research_claims",
                    "strict": True,
                    "schema": _strict_schema(schema),
                },
            }
            instructions = GUIDANCE
        else:
            # JSON object mode is a compatibility path, not schema enforcement.
            response_format = {"type": "json_object"}
            instructions = (
                GUIDANCE
                + "\nReturn a JSON object satisfying this exact schema: "
                + json.dumps(schema, ensure_ascii=False)
            )

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            "response_format": response_format,
            "stream": False,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens

        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.build_opener(_NoRedirects).open(
                request, timeout=self.timeout
            ) as response:
                if response.geturl() != endpoint:
                    raise ValueError("Research API redirected unexpectedly")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            # Deliberately omit request URL/body/provider response from errors.
            raise RuntimeError(
                f"Research API HTTP {error.code}; check endpoint, key, model and JSON mode"
            ) from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError("Research API connection failed or timed out") from None

        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("Research API response exceeds the configured size limit")
        try:
            output: Any = json.loads(raw)
            choice = output["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError("Research API output was truncated by token limit")
            message = choice["message"]["content"]
            if not isinstance(message, str):
                raise ValueError("Research API did not return JSON text content")
            return ClaimBatch.model_validate_json(message)
        except ValidationError:
            raise ValueError("Research API output failed local schema validation") from None
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise ValueError("Research API returned a malformed chat completion") from None


def model_configuration() -> dict[str, object]:
    """Expose nonsecret provider configuration for the UI."""
    base = os.environ.get("EPIPILOT_RESEARCH_API_BASE_URL", "")
    try:
        _, hostname = _endpoint(base)
        valid = True
    except ValueError:
        hostname = None
        valid = False
    return {
        "configured": valid
        and bool(os.environ.get("EPIPILOT_RESEARCH_API_KEY"))
        and bool(os.environ.get("EPIPILOT_RESEARCH_API_MODEL")),
        "provider_host": hostname,
        "model": os.environ.get("EPIPILOT_RESEARCH_API_MODEL", ""),
        "response_format": os.environ.get("EPIPILOT_RESEARCH_API_FORMAT", "json_schema"),
        "key_present": bool(os.environ.get("EPIPILOT_RESEARCH_API_KEY")),
    }
