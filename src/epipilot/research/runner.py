"""Optional exact-argv experiment runner inside the existing coding agent's terminal.

No additional LLM or autonomous executor is created. The immutable receipt
binds the actual local process invocation to its frozen metric artifact.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import time
from pathlib import Path

from epipilot.core.models import utc_now
from epipilot.research.models import ExperimentContract, RunLaunch, RunReceipt
from epipilot.research.validation import hash_file


def execute_frozen_runner(
    root: Path, contract: ExperimentContract, launch: RunLaunch
) -> RunReceipt:
    """Execute one pre-registered command without a shell and hash its outputs."""
    if not contract.runner_argv or launch.contract_id != contract.id:
        raise ValueError("controlled runner requires a matching frozen contract")
    started = time.monotonic()
    timed_out = False
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                contract.runner_argv,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=contract.runner_timeout_seconds,
                check=False,
            )
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = 124
        except OSError:
            exit_code = 127
        output.seek(0)
        digest = hashlib.sha256()
        for chunk in iter(lambda: output.read(1024 * 1024), b""):
            digest.update(chunk)
    elapsed = time.monotonic() - started
    artifact = (root / contract.artifact_ref).resolve()
    artifact_sha = (
        hash_file(artifact) if artifact.is_relative_to(root.resolve()) and artifact.is_file()
        else None
    )
    return RunReceipt(
        id=launch.id,
        contract_id=contract.id,
        source_revision=contract.git_revision,
        argv=contract.runner_argv,
        started_at=launch.started_at,
        finished_at=utc_now(),
        wall_seconds=elapsed,
        exit_code=exit_code,
        timed_out=timed_out,
        output_sha256=digest.hexdigest(),
        artifact_sha256=artifact_sha,
    )
