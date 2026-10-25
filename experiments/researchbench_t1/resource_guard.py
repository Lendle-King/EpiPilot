"""Conservative, sampled resource protection for a trusted macOS T1 runner.

The limits apply to subprocesses launched by this wrapper, not to arbitrary
unrelated user programs. Memory is sampled at 0.35 s intervals; this is a
best-effort early termination guard, NOT a kernel-enforced memory cgroup.
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
    """Insufficient headroom or a locally executed task exceeded resource limits."""


@dataclass(frozen=True)
class GuardPolicy:
    max_rss_mib: int = 1024
    min_free_gib: float = 2.0
    min_free_fraction: float = 0.18
    max_initial_cpu_percent: float = 80.0
    max_running_cpu_percent: float = 90.0
    max_processes: int = 12
    sample_interval_seconds: float = 0.35

    def validate(self) -> None:
        if not 128 <= self.max_rss_mib <= 2048:
            raise ValueError("per-process-tree RSS cap must be in [128,2048] MiB")
        if not 1.0 <= self.min_free_gib <= 8.0:
            raise ValueError("free-memory reserve must lie in [1,8] GiB")
        if not 0.1 <= self.min_free_fraction <= 0.4:
            raise ValueError("free-memory fraction must lie in [0.1,0.4]")
        if not 30 <= self.max_initial_cpu_percent <= 95:
            raise ValueError("initial CPU percentage guard outside accepted range")
        if not 50 <= self.max_running_cpu_percent <= 100:
            raise ValueError("running CPU percentage guard outside accepted range")
        if not 2 <= self.max_processes <= 32:
            raise ValueError("process-count cap outside accepted range")


POLICY = GuardPolicy()


@dataclass(frozen=True)
class ResourceSnapshot:
    total_gib: float
    available_gib: float
    cpu_percent: float
    required_free_gib: float


@dataclass(frozen=True)
class GuardedResult:
    returncode: int
    stdout: str
    stderr: str
    peak_rss_mib: float
    elapsed_seconds: float


def _psutil() -> object:
    try:
        import psutil
    except ImportError as exc:
        raise ResourceRefusal("T1 requires installed psutil to enforce resource limits") from exc
    return psutil


def assess_headroom(
    total_bytes: int,
    available_bytes: int,
    cpu_percent: float,
    policy: GuardPolicy = POLICY,
) -> ResourceSnapshot:
    """Pure fail-closed preflight, independently testable without system access."""
    policy.validate()
    gib = 1024**3
    total_gib = total_bytes / gib
    free_gib = available_bytes / gib
    required = max(policy.min_free_gib, total_gib * policy.min_free_fraction)
    if total_bytes <= 0 or available_bytes < 0 or available_bytes > total_bytes:
        raise ResourceRefusal("invalid machine memory availability reading")
    if free_gib < required:
        raise ResourceRefusal("Mac has insufficient free memory for another research task")
    if cpu_percent < 0 or cpu_percent > 100:
        raise ResourceRefusal("invalid machine CPU utilization reading")
    if cpu_percent > policy.max_initial_cpu_percent:
        raise ResourceRefusal("Mac CPU is already busy; postpone research instead of competing")
    return ResourceSnapshot(
        total_gib=round(total_gib, 2),
        available_gib=round(free_gib, 2),
        cpu_percent=round(cpu_percent, 1),
        required_free_gib=round(required, 2),
    )


def inspect_host(policy: GuardPolicy = POLICY) -> ResourceSnapshot:
    psutil = _psutil()
    mem = psutil.virtual_memory()
    utilization = psutil.cpu_percent(interval=0.25)
    return assess_headroom(int(mem.total), int(mem.available), float(utilization), policy)


@contextlib.contextmanager
def single_runner_lock() -> Iterator[None]:
    """Avoid two cooperating T1 tasks running on the same Mac account."""
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


def _terminate_group(process: subprocess.Popen[bytes], psutil: object) -> None:
    """Terminate subprocess group, including descendants, rather than only its PID."""
    try:
        root = psutil.Process(process.pid)
        descendants = root.children(recursive=True)
    except psutil.Error:
        descendants = []
    for child in reversed(descendants):
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)
    for child in descendants:
        try:
            if child.is_running():
                child.kill()
        except psutil.Error:
            pass


def guarded_run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int,
    max_rss_mib: int = 1024,
    started_marker: Path | None = None,
) -> GuardedResult:
    """Run fixed argv at low OS priority; sample its process tree and host pressure."""
    if not argv or timeout < 1 or timeout > 240:
        raise ValueError("unbounded research command or timeout")
    policy = GuardPolicy(max_rss_mib=max_rss_mib)
    inspect_host(policy)
    psutil = _psutil()
    child_env = dict(os.environ if env is None else env)
    for key in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        child_env[key] = "1"
    child_env["TOKENIZERS_PARALLELISM"] = "false"
    command = ["/usr/bin/nice", "-n", "10", *argv] if os.name == "posix" else argv
    began = time.monotonic()
    peak_rss = 0
    high_cpu_samples = 0
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=child_env,
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
                elapsed = time.monotonic() - began
                if elapsed > timeout:
                    raise ResourceRefusal("research subprocess exceeded its fixed wall timeout")
                try:
                    root = psutil.Process(process.pid)
                    descendants = root.children(recursive=True)
                    processes = [root, *descendants]
                    memory = sum(p.memory_info().rss for p in processes) / (1024**2)
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    if process.poll() is not None:
                        break
                    raise ResourceRefusal("lost monitoring of the running research process")
                except psutil.AccessDenied as exc:
                    raise ResourceRefusal("cannot measure process memory safely") from exc
                peak_rss = max(peak_rss, memory)
                if len(processes) > policy.max_processes or memory > policy.max_rss_mib:
                    raise ResourceRefusal("research subprocess exceeded its process/RSS cap")
                host_memory = psutil.virtual_memory()
                available = float(host_memory.available) / (1024**3)
                total = float(host_memory.total) / (1024**3)
                if available < max(policy.min_free_gib, total * policy.min_free_fraction):
                    raise ResourceRefusal("Mac available memory dropped below safety reserve")
                busy = float(psutil.cpu_percent(interval=None))
                high_cpu_samples = (
                    high_cpu_samples + 1 if busy > policy.max_running_cpu_percent else 0
                )
                if high_cpu_samples >= 4:
                    raise ResourceRefusal("Mac sustained CPU pressure; stopped research task")
                time.sleep(policy.sample_interval_seconds)
        except BaseException:
            _terminate_group(process, psutil)
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
            peak_rss_mib=round(peak_rss, 2),
            elapsed_seconds=round(time.monotonic() - began, 3),
        )
