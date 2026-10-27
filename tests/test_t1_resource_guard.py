"""Only whole-Mac memory usage is bounded; never perform paid calls in CI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from experiments.researchbench_t1 import resource_guard as guard


def gib(value: float) -> int:
    return int(value * (1024**3))


def test_95_percent_whole_mac_memory_ceiling() -> None:
    status = guard.assess_headroom(gib(24), gib(5.87))
    assert status.total_gib == 24
    assert status.required_free_gib == 1.2
    assert status.max_used_percent == 95.0
    assert status.used_percent < 95
    assert guard.assess_headroom(gib(24), gib(1.3)).used_percent < 95
    with pytest.raises(guard.ResourceRefusal, match="exceeds 95%"):
        guard.assess_headroom(gib(24), gib(1.0))
    with pytest.raises(guard.ResourceRefusal, match="invalid machine memory"):
        guard.assess_headroom(gib(8), gib(12))


def test_no_cpu_process_count_or_single_process_memory_caps() -> None:
    policy = guard.GuardPolicy()
    assert not hasattr(policy, "max_rss_mib")
    assert not hasattr(policy, "max_initial_cpu_percent")
    assert not hasattr(policy, "max_running_cpu_percent")
    assert not hasattr(policy, "max_processes")
    for fraction in (0.4, 0.951, 1.0):
        with pytest.raises(ValueError):
            guard.GuardPolicy(max_host_memory_used_fraction=fraction).validate()
    with pytest.raises(ValueError, match="unbounded"):
        guard.guarded_run([sys.executable, "-c", "print('x')"], timeout=3600)


def test_exclusive_lock_remains_for_run_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    with (
        guard.single_runner_lock(),
        pytest.raises(guard.ResourceRefusal, match="another EpiPilot"),
        guard.single_runner_lock(),
    ):
        pass
    with guard.single_runner_lock():
        pass


def test_real_child_runs_without_a_process_rss_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakePsutil:
        @staticmethod
        def virtual_memory() -> SimpleNamespace:
            return SimpleNamespace(total=gib(16), available=gib(12))

    monkeypatch.setattr(guard, "_psutil", lambda: FakePsutil)
    result = guard.guarded_run(
        [sys.executable, "-c", "print('global memory only')"],
        cwd=tmp_path,
        timeout=4,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "global memory only"
    assert result.peak_mac_used_percent == 25.0
    assert result.minimum_mac_available_gib == 12.0


def test_guarded_run_preserves_the_stderr_tail_after_startup_noise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakePsutil:
        @staticmethod
        def virtual_memory() -> SimpleNamespace:
            return SimpleNamespace(total=gib(16), available=gib(12))

    monkeypatch.setattr(guard, "_psutil", lambda: FakePsutil)
    code = (
        "import json, sys; "
        "sys.stdout.write(json.dumps({'type':'info','message':'Initialize ' + ('x' * 10000)})); "
        "sys.stdout.write('\\n'); "
        "sys.stdout.write(json.dumps({'type':'error','error':{'type':'invalid_request_error',"
        "'code':'bad_schema','message':'server rejected schema'}})); "
        "sys.stdout.write('\\n'); sys.stdout.flush(); "
        "sys.stderr.write('STARTUP_PREFIX_ONLY' + ('x' * 10000)); "
        "sys.stderr.write('\\nFINAL_DIAGNOSTIC: bad_schema')"
    )
    result = guard.guarded_run([sys.executable, "-c", code], cwd=tmp_path, timeout=4)
    assert result.returncode == 0
    output_rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert output_rows[0]["message"].startswith("Initialize ")
    assert output_rows[1]["type"] == "error"
    assert output_rows[1]["error"]["message"] == "server rejected schema"
    assert "FINAL_DIAGNOSTIC: bad_schema" in result.stderr
    assert "STARTUP_PREFIX_ONLY" not in result.stderr


def test_host_crossing_95_percent_kills_the_process_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakePsutil:
        calls = 0

        @classmethod
        def virtual_memory(cls) -> SimpleNamespace:
            cls.calls += 1
            available = gib(12) if cls.calls == 1 else gib(0.3)
            return SimpleNamespace(total=gib(16), available=available)

    monkeypatch.setattr(guard, "_psutil", lambda: FakePsutil)
    marker = tmp_path / "started"
    with pytest.raises(guard.ResourceRefusal, match="exceeds 95%"):
        guard.guarded_run(
            [sys.executable, "-c", "import time; time.sleep(4)"],
            timeout=3,
            started_marker=marker,
        )
    assert marker.exists()


def test_unavailable_memory_monitor_refuses_to_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        guard,
        "_psutil",
        lambda: (_ for _ in ()).throw(guard.ResourceRefusal("psutil unavailable")),
    )
    with pytest.raises(guard.ResourceRefusal, match="psutil"):
        guard.guarded_run([sys.executable, "-c", "print('must not run')"], timeout=2)
