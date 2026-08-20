"""Independent deterministic verification through safe argv-based subprocess execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from epipilot.core.models import (
    Evidence,
    EvidenceKind,
    Provenance,
    new_evidence_id,
)
from epipilot.verification.pipeline import CheckResult, VerificationRequest


@dataclass(frozen=True, slots=True)
class CommandVerificationCheck:
    """Run one deterministic verification command outside the coding-agent executor."""

    name: str
    argv: tuple[str, ...]
    cwd: Path
    scope: str
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("verification command name must not be empty")
        if not self.argv or any(not item for item in self.argv):
            raise ValueError("verification argv must contain non-empty entries")
        if not self.scope.strip():
            raise ValueError("verification scope must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("verification timeout must be positive")

    async def run(self, request: VerificationRequest) -> CheckResult:
        process = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=str(self.cwd),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        timed_out = False
        try:
            return_code = await asyncio.wait_for(
                process.wait(),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
            process.kill()
            return_code = await process.wait()

        passed = not timed_out and return_code == 0
        if timed_out:
            summary = f"{self.name} timed out after {self.timeout_seconds:g}s"
        else:
            summary = f"{self.name} exited with code {return_code}"

        evidence = Evidence(
            id=new_evidence_id(),
            kind=EvidenceKind.DETERMINISTIC_CHECK,
            summary=summary,
            provenance=Provenance(
                source=f"command:{self.name}",
                scope=f"{self.scope}/task/{request.task.id}",
            ),
            independently_verified=True,
        )
        return CheckResult(name=self.name, passed=passed, evidence=evidence)
