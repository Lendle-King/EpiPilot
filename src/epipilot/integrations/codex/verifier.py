"""Independent deterministic verifier used by the Codex research bridge."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from epipilot.core.models import EvidenceId, EvidenceKind, Provenance
from epipilot.integrations.codex.bridge import CodexResearchBridge


@dataclass(frozen=True, slots=True)
class CommandProbeResult:
    """Result of one shell-free deterministic verification command."""

    evidence_id: EvidenceId
    passed: bool
    return_code: int | None
    timed_out: bool


@dataclass(slots=True)
class CommandProbeVerifier:
    """Run an argv command and admit only the derived result as verified evidence."""

    bridge: CodexResearchBridge

    def run(
        self,
        *,
        project_id: str,
        name: str,
        argv: tuple[str, ...],
        cwd: Path,
        scope: str,
        timeout_seconds: float = 300.0,
    ) -> CommandProbeResult:
        if not name.strip():
            raise ValueError("verification command name must not be empty")
        if not argv or any(not item for item in argv):
            raise ValueError("verification argv must contain non-empty entries")
        if not scope.strip():
            raise ValueError("verification scope must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("verification timeout must be positive")

        timed_out = False
        return_code: int | None
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
            )
            return_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            return_code = None

        passed = not timed_out and return_code == 0
        if timed_out:
            summary = f"{name} timed out after {timeout_seconds:g}s"
        else:
            summary = f"{name} exited with code {return_code}"

        evidence_id = self.bridge._record_verified_evidence(
            project_id=project_id,
            kind=EvidenceKind.DETERMINISTIC_CHECK,
            summary=summary,
            provenance=Provenance(
                source=f"command:{name}",
                scope=scope,
            ),
        )
        return CommandProbeResult(
            evidence_id=evidence_id,
            passed=passed,
            return_code=return_code,
            timed_out=timed_out,
        )
