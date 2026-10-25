"""Optional exact-argv experiment runner inside the existing coding agent's terminal.

No additional LLM or autonomous executor is created. The immutable receipt
binds the actual local process invocation to its frozen metric artifact.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from epipilot.core.models import utc_now
from epipilot.research.models import ExperimentContract, RunLaunch, RunReceipt
from epipilot.research.validation import hash_file


def receipt_file(root: Path, run_id: str) -> Path:
    """The event log is canonical; a separate local sidecar bridges the crash window."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ValueError("invalid controlled run identifier")
    return root / ".research" / "receipts" / f"{run_id}.json"


def _persist_receipt(root: Path, receipt: RunReceipt) -> None:
    """Write before the completion event, so restart can reconcile without relaunch."""
    destination = receipt_file(root, receipt.id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("runner receipt sidecar already exists")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=".partial-receipt-",
            delete=False,
        ) as output:
            temp_path = Path(output.name)
            output.write(receipt.model_dump_json())
            output.flush()
            os.fsync(output.fileno())
        temp_path.replace(destination)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def recover_receipt(
    root: Path, contract: ExperimentContract, launch: RunLaunch
) -> RunReceipt | None:
    """Adopt a complete sidecar only when its frozen source/argv/artifact still match."""
    path = receipt_file(root, launch.id)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 128 * 1024:
        raise ValueError("invalid runner recovery sidecar")
    receipt = RunReceipt.model_validate_json(path.read_bytes())
    if (
        receipt.id != launch.id
        or receipt.contract_id != contract.id
        or receipt.started_at != launch.started_at
        or receipt.source_revision != contract.git_revision
        or receipt.argv != contract.runner_argv
    ):
        raise ValueError("recovery sidecar differs from frozen run")
    artifact = (root / contract.artifact_ref).resolve()
    if receipt.artifact_sha256 is None:
        if artifact.exists():
            raise ValueError("result artifact exists but was not in the original run receipt")
    elif (
        not artifact.is_relative_to(root.resolve())
        or not artifact.is_file()
        or hash_file(artifact) != receipt.artifact_sha256
    ):
        raise ValueError("recovery artifact hash differs from finished run")
    return receipt


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
        hash_file(artifact)
        if artifact.is_relative_to(root.resolve()) and artifact.is_file()
        else None
    )
    receipt = RunReceipt(
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
    _persist_receipt(root, receipt)
    return receipt
