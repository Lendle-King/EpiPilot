"""Text-only Responses gateway: exact model, counted HTTP, no retries or hosted tools.

Run in a trusted controller identity. Agents receive only a per-cell capability token.
Network isolation must prevent direct provider access. No live approval is bundled here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Protocol

from epipilot.benchfirst.contracts import _no_duplicate_properties, _reject_constant
from epipilot.benchfirst.metering import Meter, Usage

MAX_BODY = 1_048_576
MAX_REPLY = 8_388_608


@dataclass(frozen=True)
class WireReply:
    status: int
    content_type: str
    body: bytes


class Provider(Protocol):
    def post(self, endpoint: str, payload: bytes) -> WireReply: ...


def object_json(raw: bytes) -> dict[str, object]:
    value: object = json.loads(
        raw, object_pairs_hook=_no_duplicate_properties, parse_constant=_reject_constant
    )
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


class OpenAIHTTPS:
    """Fixed HTTPS destination; never forwards client headers, redirects or proxy env."""

    def __init__(self, api_key: str, timeout: int = 90) -> None:
        self.api_key, self.timeout = api_key, timeout

    def post(self, endpoint: str, payload: bytes) -> WireReply:
        if endpoint not in {"responses", "responses/input_tokens"}:
            raise ValueError("unapproved endpoint")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(
                self,
                req: urllib.request.Request,
                fp: object,
                code: int,
                msg: str,
                headers: object,
                newurl: str,
            ) -> None:
                return None

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(
            "https://api.openai.com/v1/" + endpoint,
            data=payload,
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read(MAX_REPLY + 1)
            if len(body) > MAX_REPLY:
                raise ValueError("upstream reply limit; retain full reservation")
            return WireReply(response.status, response.headers.get_content_type(), body)


class Gateway:
    def __init__(
        self, meter: Meter, provider: Provider, *, authorized: Callable[[], bool] = lambda: True
    ) -> None:
        self.meter, self.provider = meter, provider
        self.authorized = authorized
        self.lock = threading.Lock()

    def payload(self, raw: bytes) -> dict[str, object]:
        if len(raw) > MAX_BODY:
            raise ValueError("request too large")
        body = object_json(raw)
        allowed = {
            "model",
            "input",
            "instructions",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "max_output_tokens",
            "reasoning",
            "text",
            "temperature",
            "top_p",
            "stream",
            "store",
            "include",
            "metadata",
            "service_tier",
            "truncation",
            "previous_response_id",
            "prompt_cache_key",
            "prompt_cache_retention",
        }
        if set(body) - allowed or body.get("model") != self.meter.budget.model:
            raise ValueError("unsupported field or unapproved model")
        if body.get("previous_response_id") or body.get("service_tier", "default") != "default":
            raise ValueError("stateful context or service tier is not approved")
        if body.get("prompt_cache_retention", "in_memory") != "in_memory":
            raise ValueError("extended cache pricing is not approved")
        tools = body.get("tools", [])
        if not isinstance(tools, list) or any(
            not isinstance(tool, dict) or tool.get("type") not in {"function", "custom"}
            for tool in tools
        ):
            raise ValueError("only local function/custom tools are permitted")

        # No files, images, audio, hosted tools or remote context expansion.
        def check(value: object) -> None:
            if isinstance(value, dict):
                kind = value.get("type")
                if isinstance(kind, str) and any(x in kind for x in ("image", "audio", "file")):
                    raise ValueError("text-only gateway")
                for nested in value.values():
                    check(nested)
            elif isinstance(value, list):
                for nested in value:
                    check(nested)

        check(body)
        limit = body.get("max_output_tokens", self.meter.budget.max_output_tokens)
        if type(limit) is not int or limit <= 0:
            raise ValueError("invalid output limit")
        body.update(
            max_output_tokens=min(limit, self.meter.budget.max_output_tokens),
            store=False,
            service_tier="default",
            truncation="disabled",
        )
        return body

    def handle(self, raw: bytes) -> WireReply:
        try:
            return self._handle(raw)
        except Exception:
            self.meter.denied(hashlib.sha256(raw).hexdigest())
            raise

    def _handle(self, raw: bytes) -> WireReply:
        with self.lock:
            if not self.authorized():
                raise ValueError("approval expired")
            body = self.payload(raw)
            payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            attempt = self.meter.reserve(hashlib.sha256(payload).hexdigest())
            try:
                count_payload = {
                    k: v
                    for k, v in body.items()
                    if k
                    in {
                        "model",
                        "input",
                        "instructions",
                        "tools",
                        "tool_choice",
                        "parallel_tool_calls",
                        "reasoning",
                        "text",
                        "truncation",
                    }
                }
                self.meter.wire(attempt, "input_tokens")
                counted = self.provider.post(
                    "responses/input_tokens", json.dumps(count_payload).encode()
                )
                count = object_json(counted.body).get("input_tokens")
                if (
                    counted.status != 200
                    or type(count) is not int
                    or not 0 <= count <= self.meter.budget.max_input_tokens
                ):
                    raise ValueError("token count unavailable or input budget exceeded")
                if not self.authorized():
                    raise ValueError("approval expired after token count")
                self.meter.wire(attempt, "responses")
                reply = self.provider.post("responses", payload)
                if reply.status != 200:
                    raise ValueError("provider rejected request; reservation retained")
                result = self.terminal(reply)
                if result.get("model") != self.meter.budget.model:
                    raise ValueError("provider model mismatch")
                reported = result.get("usage")
                if not isinstance(reported, dict):
                    raise ValueError("missing provider usage")
                usage = Usage.model_validate(
                    {k: reported.get(k) for k in ("input_tokens", "output_tokens")}
                )
                response_id = result.get("id")
                if not isinstance(response_id, str) or not response_id:
                    raise ValueError("missing provider response ID")
                self.meter.finish(attempt, usage, response_id)
                return reply
            except Exception:
                # Unknown outcomes remain fully charged. Never reissue automatically.
                self.meter.interrupt(attempt)
                raise

    @staticmethod
    def terminal(reply: WireReply) -> dict[str, object]:
        if reply.content_type == "application/json":
            return object_json(reply.body)
        if reply.content_type != "text/event-stream":
            raise ValueError("unexpected provider content type")
        terminals = []
        for line in reply.body.splitlines():
            if line.startswith(b"data: ") and line != b"data: [DONE]":
                event = object_json(line[6:])
                if event.get("type") in {"response.completed", "response.incomplete"}:
                    terminals.append(event.get("response"))
        if len(terminals) != 1 or not isinstance(terminals[0], dict):
            raise ValueError("stream has no unique terminal usage")
        return terminals[0]


def serve(gateway: Gateway, capability: str, host: str = "127.0.0.1", port: int = 8080) -> None:
    if len(capability) < 32:
        raise ValueError("short gateway capability")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.connection.settimeout(10)
            if (
                self.path != "/v1/responses"
                or self.headers.get("Transfer-Encoding")
                or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + capability
                )
            ):
                self.send_error(403)
                return
            try:
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= size <= MAX_BODY:
                    raise ValueError("request size")
                reply = gateway.handle(self.rfile.read(size))
            except Exception:
                # Do not echo prompts, provider errors or credentials.
                self.send_error(409, "request denied; inspect private request audit")
                return
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.content_type)
            self.send_header("Content-Length", str(len(reply.body)))
            self.end_headers()
            self.wfile.write(reply.body)

        def log_message(self, format: str, *args: object) -> None:
            pass  # Request audit is structured; never log request bodies/auth.

    HTTPServer((host, port), Handler).serve_forever()


def api_key_from_environment() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("provider key unavailable in trusted controller")
    return key
