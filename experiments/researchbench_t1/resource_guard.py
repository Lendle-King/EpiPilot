"""Mac T1 subprocess supervision with a whole-machine 95% memory ceiling.

Only HOST physical memory use is constrained. No per-process RSS, CPU usage,
process-count or forecast-memory budget is enforced. Supervision is sampled,
not a macOS kernel-enforced memory quota. Tasks retain timeout/cancellation
and an exclusive lock to prevent overlapping experiments.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


class ResourceRefusal(RuntimeError):
    """Machine memory cap or bounded-subprocess safety requirement was violated."""


@dataclass(frozen=True)
class GuardPolicy:
    max_host_memory_used_fraction: float = 0.95
    sample_interval_seconds: float = 0.35

    def validate(self) -> None:
        if not 0.50 <= self.max_host_memory_used_fraction <= 0.95:
            raise ValueError("whole-machine memory usage cap must be in [50%,95%]")
        if not 0.1 <= self.sample_interval_seconds <= 2:
            raise ValueError("memory-monitor interval must be within [0.1,2] seconds")


POLICY = GuardPolicy()


@dataclass(frozen=True)
class ResourceSnapshot:
    total_gib: float
    available_gib: float
    required_free_gib: float
    used_percent: float
    max_used_percent: float


@dataclass(frozen=True)
class GuardedResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    peak_mac_used_percent: float
    minimum_mac_available_gib: float


def _psutil() -> object:
    try:
        import psutil
    except ImportError as exc:
        raise ResourceRefusal("psutil is required for total Mac memory monitoring") from exc
    return psutil


def assess_headroom(
    total_bytes: int,
    available_bytes: int,
    policy: GuardPolicy = POLICY,
) -> ResourceSnapshot:
    """Fail closed if system-wide physical memory is already over the limit."""
    policy.validate()
    if total_bytes <= 0 or available_bytes < 0 or available_bytes > total_bytes:
        raise ResourceRefusal("invalid machine memory availability reading")
    required_bytes = total_bytes * (1 - policy.max_host_memory_used_fraction)
    if available_bytes < required_bytes:
        raise ResourceRefusal("Mac whole-machine physical memory usage exceeds 95%")
    gib = 1024**3
    return ResourceSnapshot(
        total_gib=round(total_bytes / gib, 2),
        available_gib=round(available_bytes / gib, 2),
        required_free_gib=round(required_bytes / gib, 2),
        used_percent=round((1 - available_bytes / total_bytes) * 100, 2),
        max_used_percent=round(policy.max_host_memory_used_fraction * 100, 2),
    )


def inspect_host(policy: GuardPolicy = POLICY) -> ResourceSnapshot:
    mem = _psutil().virtual_memory()
    return assess_headroom(int(mem.total), int(mem.available), policy)


@contextlib.contextmanager
def single_runner_lock() -> Iterator[None]:
    """Only one cooperating T1 controller may use the Mac at a time."""
    base = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    lock = base / "epipilot_t1_agent_exclusive.lock"
    with lock.open("a+b") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResourceRefusal("another EpiPilot T1 task already owns this Mac") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate the isolated OS process group after a memory/time breach."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)


def guarded_run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int,
    started_marker: Path | None = None,
) -> GuardedResult:
    """Execute a fixed command, monitoring only total host physical memory."""
    if not argv or timeout < 1 or timeout > 240:
        raise ValueError("unbounded research command or timeout")
    initial = inspect_host()
    psutil = _psutil()
    began = time.monotonic()
    peak_used = initial.used_percent
    min_free = initial.available_gib
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise ResourceRefusal("could not launch bounded research subprocess") from exc
        try:
            if started_marker is not None:
                started_marker.write_text(str(time.time()), encoding="utf-8")
            while process.poll() is None:
                if time.monotonic() - began > timeout:
                    raise ResourceRefusal("research subprocess exceeded fixed wall timeout")
                mem = psutil.virtual_memory()
                sample = assess_headroom(int(mem.total), int(mem.available))
                peak_used = max(peak_used, sample.used_percent)
                min_free = min(min_free, sample.available_gib)
                time.sleep(POLICY.sample_interval_seconds)
        except BaseException:
            _terminate_group(process)
            raise
        stdout.seek(0)
        stderr.seek(0)
        out_bytes = stdout.read(200_001)
        err_bytes = stderr.read(8_192)
        if len(out_bytes) > 200_000:
            raise ResourceRefusal("research subprocess generated excessive standard output")
        return GuardedResult(
            returncode=int(process.returncode),
            stdout=out_bytes.decode("utf-8", errors="replace"),
            stderr=err_bytes.decode("utf-8", errors="replace"),
            elapsed_seconds=round(time.monotonic() - began, 3),
            peak_mac_used_percent=round(peak_used, 2),
            minimum_mac_available_gib=round(min_free, 2),
        )
