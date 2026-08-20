"""Composable verification pipeline with evidence-strength enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from epipilot.core.models import Evidence, EvidenceKind, Task


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    """Inputs made available to one verification run."""

    task: Task
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome from one independent or semantic verification check."""

    name: str
    passed: bool
    evidence: Evidence

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("verification check name must not be empty")


class VerificationCheck(Protocol):
    """Boundary implemented by deterministic, runtime, or semantic checks."""

    async def run(self, request: VerificationRequest) -> CheckResult:
        """Run the check and return auditable evidence."""
        ...


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """Aggregated verifier decision and all evidence produced by its checks."""

    passed: bool
    results: tuple[CheckResult, ...]

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(result.evidence for result in self.results)

    def completion_evidence(self) -> Evidence:
        """Return evidence strong enough to authorize ``TaskStatus.PASSED``.

        The method fails closed when the pipeline contains only executor self-report or
        otherwise unverified observations.
        """
        for result in self.results:
            evidence = result.evidence
            if (
                result.passed
                and evidence.independently_verified
                and evidence.kind is not EvidenceKind.EXECUTOR_REPORT
            ):
                return evidence
        raise ValueError("verification outcome contains no independent completion evidence")


@dataclass(frozen=True, slots=True)
class VerifierPipeline:
    """Run verification checks in deterministic order and aggregate their results."""

    checks: tuple[VerificationCheck, ...]

    def __post_init__(self) -> None:
        if not self.checks:
            raise ValueError("verification pipeline requires at least one check")

    async def verify(self, request: VerificationRequest) -> VerificationOutcome:
        results: list[CheckResult] = []
        for check in self.checks:
            results.append(await check.run(request))

        all_checks_passed = all(result.passed for result in results)
        has_independent_evidence = any(
            result.passed
            and result.evidence.independently_verified
            and result.evidence.kind is not EvidenceKind.EXECUTOR_REPORT
            for result in results
        )
        return VerificationOutcome(
            passed=all_checks_passed and has_independent_evidence,
            results=tuple(results),
        )
