import json
from pathlib import Path

import pytest
from tests.benchfirst.test_metering import policy

from epipilot.benchfirst.gateway import Gateway, WireReply
from epipilot.benchfirst.metering import Meter


class Provider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    def post(self, endpoint: str, payload: bytes) -> WireReply:
        self.calls.append(endpoint)
        if self.fail:
            raise TimeoutError("synthetic timeout")
        body = json.loads(payload)
        assert body["model"] == "test-model"
        if endpoint == "responses/input_tokens":
            return WireReply(200, "application/json", b'{"input_tokens":25}')
        assert body["max_output_tokens"] == 20
        return WireReply(
            200,
            "application/json",
            json.dumps(
                {
                    "id": "resp_test",
                    "model": "test-model",
                    "usage": {"input_tokens": 25, "output_tokens": 7},
                }
            ).encode(),
        )


def test_gateway_counts_all_attempts_and_rejects_model_and_hosted_tools(tmp_path: Path) -> None:
    provider = Provider()
    meter = Meter(tmp_path / "audit.db", policy())
    gate = Gateway(meter, provider)
    for body in ({"model": "other"}, {"model": "test-model", "tools": [{"type": "web_search"}]}):
        with pytest.raises(ValueError):
            gate.handle(json.dumps(body).encode())
    assert provider.calls == []
    gate.handle(b'{"model":"test-model","input":"public task","stream":false}')
    assert provider.calls == ["responses/input_tokens", "responses"]
    assert meter.summary().http_requests == 2
    assert meter.summary().estimated_nusd == 120


def test_failure_is_spent_and_never_retried(tmp_path: Path) -> None:
    provider = Provider()
    provider.fail = True
    meter = Meter(tmp_path / "audit.db", policy())
    with pytest.raises(TimeoutError):
        Gateway(meter, provider).handle(b'{"model":"test-model","input":"public task"}')
    assert len(provider.calls) == 1
    assert meter.summary().held_nusd == 400
    assert meter.summary().unknown_attempts == 1


def test_sse_terminal_usage_and_duplicate_keys(tmp_path: Path) -> None:
    class SSEProvider(Provider):
        def post(self, endpoint: str, payload: bytes) -> WireReply:
            reply = super().post(endpoint, payload)
            if endpoint == "responses":
                return WireReply(
                    200,
                    "text/event-stream",
                    b"event: response.completed\ndata: "
                    + json.dumps(
                        {"type": "response.completed", "response": json.loads(reply.body)}
                    ).encode()
                    + b"\n\n",
                )
            return reply

    meter = Meter(tmp_path / "audit.db", policy())
    gateway = Gateway(meter, SSEProvider())
    with pytest.raises(ValueError):
        gateway.handle(b'{"model":"other","model":"test-model"}')
    assert (
        gateway.handle(b'{"model":"test-model","stream":true}').content_type == "text/event-stream"
    )
    assert meter.summary().unknown_attempts == 0
