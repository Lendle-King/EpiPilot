"""Provider-free tests: request reservations must survive retries and crashes."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from epipilot.benchfirst.metering import Meter, RequestBudget, Usage


def policy(**kwargs: object) -> RequestBudget:
    return RequestBudget.model_validate(
        {
            "cell_id": "test-cell",
            "plan_sha256": "a" * 64,
            "model": "test-model",
            "approval_ref": "synthetic-test-only",
            "max_attempts": 2,
            "max_http_requests": 4,
            "max_input_tokens": 100,
            "max_output_tokens": 20,
            "input_nusd_per_token": 2,
            "output_nusd_per_token": 10,
            "max_cost_nusd": 800,
            "wall_seconds": 60,
            **kwargs,
        }
    )


def test_crash_reservations_remain_spent_and_identity_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "meter.sqlite3"
    meter = Meter(path, policy())
    attempt = meter.reserve("b" * 64)
    assert meter.summary().held_nusd == 400
    with pytest.raises(ValueError, match="pending"):
        Meter(path, policy()).reserve("c" * 64)
    meter.interrupt(attempt)
    assert Meter(path, policy()).summary().held_nusd == 400
    meter.reserve("c" * 64)
    with pytest.raises(ValueError):
        Meter(path, policy(max_cost_nusd=801))


def test_concurrent_requests_cannot_overspend(tmp_path: Path) -> None:
    meter = Meter(tmp_path / "meter.sqlite3", policy(max_attempts=1, max_http_requests=2))

    def reserve(index: int) -> bool:
        try:
            meter.reserve(str(index) * 64)
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(4))) == 1
    assert meter.summary().attempts == 1


def test_each_wire_request_counted_duplicate_denied_and_usage_not_invoice(tmp_path: Path) -> None:
    meter = Meter(tmp_path / "meter.sqlite3", policy())
    attempt = meter.reserve("b" * 64)
    meter.wire(attempt, "input_tokens")
    meter.wire(attempt, "responses")
    meter.finish(attempt, Usage(input_tokens=25, output_tokens=7), "resp_test")
    assert meter.summary().estimated_nusd == 120
    assert meter.summary().provider_billed_nusd is None
    with pytest.raises(ValueError, match="duplicate"):
        meter.reserve("b" * 64)
    with pytest.raises(ValueError):
        meter.finish(attempt, Usage(input_tokens=1, output_tokens=1), "again")


def test_unknown_and_out_of_bounds_usage_keep_full_reservation(tmp_path: Path) -> None:
    meter = Meter(tmp_path / "meter.sqlite3", policy())
    attempt = meter.reserve("b" * 64)
    with pytest.raises(ValueError, match="usage"):
        meter.finish(attempt, Usage(input_tokens=101, output_tokens=1), "resp_test")
    assert meter.summary().held_nusd == 400
    meter.interrupt(attempt)
    assert meter.summary().held_nusd == 400


def test_http_and_wall_caps_fail_closed(tmp_path: Path) -> None:
    clock = [0.0]
    meter = Meter(tmp_path / "meter.sqlite3", policy(max_http_requests=1), now=lambda: clock[0])
    attempt = meter.reserve("b" * 64)
    meter.wire(attempt, "input_tokens")
    with pytest.raises(ValueError, match="HTTP"):
        meter.wire(attempt, "responses")
    clock[0] = 61
    meter.interrupt(attempt)
    with pytest.raises(ValueError, match="wall"):
        meter.reserve("c" * 64)
