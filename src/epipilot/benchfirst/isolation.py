"""Offline Docker boundary. Never mounts credentials, evaluator labels or the Docker socket."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from epipilot.benchfirst.offline import Value


class Limits(Value):
    cpus: int = Field(default=2, ge=1, le=2)
    memory_mb: int = Field(default=2048, ge=128, le=4096)
    pids: int = Field(default=64, ge=16, le=128)
    seconds: int = Field(default=90, ge=1, le=1800)


class ProcessResult(Value):
    argv: tuple[str, ...]
    exit_code: int
    timed_out: bool
    wall_seconds: float
    log_sha256: str
    # Raw logs stay local to the private run workspace.
    log_path: Path
    stop_reason: str = ""


class OfflineDocker:
    def __init__(self, image: str, limits: Limits | None = None) -> None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("sandbox requires immutable local Docker image ID")
        self.image, self.limits = image, limits or Limits()

    def argv(
        self, name: str, harness: Path, source: Path, work: Path, command: tuple[str, ...]
    ) -> tuple[str, ...]:
        for path in (harness, source, work):
            if not path.is_dir() or path.is_symlink() or "," in str(path):
                raise ValueError("unsafe sandbox mount")
        return (
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65534:65534",
            f"--cpus={self.limits.cpus}",
            f"--memory={self.limits.memory_mb}m",
            f"--memory-swap={self.limits.memory_mb}m",
            f"--pids-limit={self.limits.pids}",
            "--ulimit",
            "fsize=16777216:16777216",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=128m",
            "--mount",
            f"type=bind,src={harness.resolve()},dst=/harness,readonly",
            "--mount",
            f"type=bind,src={source.resolve()},dst=/framework,readonly",
            "--mount",
            f"type=bind,src={work.resolve()},dst=/work",
            "--workdir=/work",
            "--env",
            "HOME=/tmp",
            "--env",
            "WANDB_MODE=disabled",
            "--env",
            "EPIPILOT_S0_CONTAINER=1",
            "--env",
            "HF_HUB_OFFLINE=1",
            "--env",
            "PYTHONPATH=/harness:/framework:/framework/src",
            "--env",
            "LOGGING_DIR=/work/logs",
            "--env",
            "MLE_BENCH_DATA_DIR=/work/data",
            "--env",
            "SUPERIMAGE_DIR=/work/sif",
            "--env",
            "USER=offline",
            self.image,
            *command,
        )

    def run(
        self,
        harness: Path,
        source: Path,
        work: Path,
        command: tuple[str, ...],
        log: Path,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> ProcessResult:
        from epipilot.benchfirst.offline import digest

        name = "epipilot-s0-" + uuid4().hex
        argv = self.argv(name, harness, source, work, command)
        start = time.monotonic()
        timed_out = False
        # Never pipe unlimited candidate output into host memory.
        with log.open("xb") as handle:
            process = subprocess.Popen(
                argv, stdout=handle, stderr=subprocess.STDOUT, env=environment
            )
            reason = ""
            next_sample = 0.0
            try:
                while process.poll() is None:
                    elapsed = time.monotonic() - start
                    if elapsed >= self.limits.seconds:
                        timed_out, reason = True, "wall_limit"
                    elif log.stat().st_size > 4_194_304:
                        reason = "log_limit"
                    elif elapsed >= next_sample:
                        next_sample = elapsed + 1
                        if host_memory_fraction() >= 0.95:
                            reason = "host_ram_95_percent"
                    if reason:
                        subprocess.run(
                            ["docker", "kill", name], capture_output=True, timeout=15, check=False
                        )
                        process.kill()
                        process.wait(timeout=15)
                        break
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        continue
                code = 124 if timed_out else (125 if reason else process.returncode)
            finally:
                subprocess.run(
                    ["docker", "rm", "-f", name], capture_output=True, timeout=15, check=False
                )
        result = ProcessResult(
            argv=argv,
            exit_code=code,
            timed_out=timed_out,
            wall_seconds=time.monotonic() - start,
            log_sha256=digest(log),
            log_path=log,
            stop_reason=reason,
        )
        log.with_suffix(".process.json").write_text(
            json.dumps(result.model_dump(mode="json"), indent=2)
        )
        return result


def host_memory_fraction() -> float:
    """Sample physical memory pressure; fail closed on unsupported/invalid telemetry."""
    if sys.platform == "darwin":
        total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=5))
        raw = subprocess.check_output(["vm_stat"], text=True, timeout=5)
        page = re.search(r"page size of (\d+) bytes", raw)
        if page is None:
            raise ValueError("host memory telemetry unavailable")
        values = dict(re.findall(r"([^\n:]+):\s+(\d+)\.", raw))
        available = sum(
            int(values[k]) for k in ("Pages free", "Pages inactive", "Pages speculative")
        ) * int(page.group(1))
    elif sys.platform == "linux":
        values = dict(re.findall(r"(\w+):\s+(\d+) kB", Path("/proc/meminfo").read_text()))
        total, available = int(values["MemTotal"]), int(values["MemAvailable"])
    else:
        raise ValueError("host memory telemetry unsupported")
    if not 0 < available <= total:
        raise ValueError("host memory telemetry invalid")
    return 1 - available / total
