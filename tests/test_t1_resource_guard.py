"""Strict Mac resource-pressure checks without paid models or heavy CI workloads."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from experiments.researchbench_t1 import resource_guard as guard


def gib(value: float) -> int:
    return int(value * (1024**3))


def test_headroom_requires_available_ram_and_idle_cpu() -> None:
    safe = guard.assess_headroom(gib(24), gib(14), 8.0)
    assert safe.total_gib == 24
    assert safe.available_gib == 14
    assert safe.required_free_gib == 1.2
    assert guard.assess_headroom(gib(24), gib(1.4), 8.0).required_free_gib == 1.2
    with pytest.raises(guard.ResourceRefusal, match="insufficient free memory"):
        guard.assess_headroom(gib(24), gib(1.0), 10)
    with pytest.raises(guard.ResourceRefusal, match="already busy"):
        guard.assess_headroom(gib(24), gib(12), 94)
    with pytest.raises(guard.ResourceRefusal, match="invalid machine memory"):
        guard.assess_headroom(gib(8), gib(12), 20)


def test_invalid_limits_fail_closed() -> None:
    for rss_mib in (1, 4096):
        with pytest.raises(ValueError):
            guard.GuardPolicy(max_rss_mib=rss_mib).validate()
    for fraction in (0.01, 0.049, 0.6):
        with pytest.raises(ValueError):
            guard.GuardPolicy(min_free_fraction=fraction).validate()
    with pytest.raises(ValueError, match="unbounded"):
        guard.guarded_run([sys.executable, "-c", "print('x')"], timeout=3600)


def test_exclusive_lock_blocks_second_research_task(
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


def test_guarded_process_reports_rss_and_prevents_arbitrary_environment_leakage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakePsutil:
        Error = RuntimeError
        NoSuchProcess = ProcessLookupError
        ZombieProcess = ProcessLookupError
        AccessDenied = PermissionError

        @staticmethod
        def virtual_memory() -> SimpleNamespace:
            return SimpleNamespace(total=gib(16), available=gib(12))

        @staticmethod
        def cpu_percent(interval: float | None = None) -> float:
            return 5.0

        @staticmethod
        def Process(pid: int) -> object:
            class FakeProcess:
                def children(self, recursive: bool = False) -> list[object]:
                    return []

                def memory_info(self) -> SimpleNamespace:
                    return SimpleNamespace(rss=55 * 1024**2)

            return FakeProcess()

    monkeypatch.setattr(guard, "_psutil", lambda: FakePsutil)
    outcome = guard.guarded_run(
        [sys.executable, "-c", "print('bounded')"],
        timeout=3,
        cwd=tmp_path,
        max_rss_mib=256,
    )
    assert outcome.returncode == 0
    assert outcome.stdout.strip() == "bounded"
    assert 0 <= outcome.peak_rss_mib <= 256
    assert outcome.elapsed_seconds >= 0


def test_overbudget_process_is_terminated_without_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakePsutil:
        Error = RuntimeError
        NoSuchProcess = ProcessLookupError
        ZombieProcess = ProcessLookupError
        AccessDenied = PermissionError

        @staticmethod
        def virtual_memory() -> SimpleNamespace:
            return SimpleNamespace(total=gib(16), available=gib(12))

        @staticmethod
        def cpu_percent(interval: float | None = None) -> float:
            return 3.0

        @staticmethod
        def Process(pid: int) -> object:
            class FakeProcess:
                def children(self, recursive: bool = False) -> list[object]:
                    return []

                def memory_info(self) -> SimpleNamespace:
                    return SimpleNamespace(rss=600 * 1024**2)

            return FakeProcess()

    monkeypatch.setattr(guard, "_psutil", lambda: FakePsutil)
    marker = tmp_path / "attempt.txt"
    with pytest.raises(guard.ResourceRefusal, match="RSS cap"):
        guard.guarded_run(
            [sys.executable, "-c", "import time; time.sleep(4)"],
            timeout=2,
            max_rss_mib=256,
            started_marker=marker,
        )
    assert marker.exists()


def test_unavailable_monitoring_refuses_to_start_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        guard,
        "_psutil",
        lambda: (_ for _ in ()).throw(guard.ResourceRefusal("psutil unavailable")),
    )
    with pytest.raises(guard.ResourceRefusal, match="psutil"):
        guard.guarded_run([sys.executable, "-c", "print('should not run')"], timeout=3)


def test_95_percent_ram_limit_retains_peak_memory_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SnapshotOnly:
        available = gib(5.84)

        @staticmethod
        def virtual_memory() -> SimpleNamespace:
            return SimpleNamespace(total=gib(24), available=SnapshotOnly.available)

        @staticmethod
        def cpu_percent(interval: float | None = None) -> float:
            return 26.9

    monkeypatch.setattr(guard, "_psutil", lambda: SnapshotOnly)
    sample = guard.inspect_host()
    assert sample.available_gib == 5.84
    assert sample.required_free_gib == 1.2
    assert guard.inspect_host(planned_rss_mib=1536).available_gib == 5.84

    # Total usage can approach 95%, but new Codex needs its full
    # 1.5 GiB RSS allowance plus an additional 0.5 GiB prelaunch buffer.
    SnapshotOnly.available = gib(3.0)
    assert guard.inspect_host().available_gib == 3.0
    with pytest.raises(guard.ResourceRefusal, match="lacks headroom"):
        guard.inspect_host(planned_rss_mib=1536)
    with pytest.raises(guard.ResourceRefusal, match="lacks headroom"):
        guard.guarded_run(
            [sys.executable, "-c", "print('must not execute')"],
            timeout=3,
            max_rss_mib=1536,
        )
    SnapshotOnly.available = gib(1.0)
    with pytest.raises(guard.ResourceRefusal, match="insufficient free memory"):
        guard.inspect_host()
