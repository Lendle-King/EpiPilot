from __future__ import annotations

import sys
from pathlib import Path

import pytest

from epipilot.core.models import EvidenceKind, Task, new_task_id
from epipilot.verification.command import CommandVerificationCheck
from epipilot.verification.pipeline import VerificationRequest


@pytest.mark.asyncio
async def test_command_verifier_passes_on_zero_exit(tmp_path: Path) -> None:
    check = CommandVerificationCheck(
        name="smoke",
        argv=(sys.executable, "-c", "raise SystemExit(0)"),
        cwd=tmp_path,
        scope="project/test",
    )
    request = VerificationRequest(task=Task(id=new_task_id(), objective="Verify feature"))

    result = await check.run(request)

    assert result.passed
    assert result.evidence.kind is EvidenceKind.DETERMINISTIC_CHECK
    assert result.evidence.independently_verified


@pytest.mark.asyncio
async def test_command_verifier_fails_on_nonzero_exit(tmp_path: Path) -> None:
    check = CommandVerificationCheck(
        name="smoke",
        argv=(sys.executable, "-c", "raise SystemExit(7)"),
        cwd=tmp_path,
        scope="project/test",
    )
    request = VerificationRequest(task=Task(id=new_task_id(), objective="Verify feature"))

    result = await check.run(request)

    assert not result.passed
    assert "code 7" in result.evidence.summary
