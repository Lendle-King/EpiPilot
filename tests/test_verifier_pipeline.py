from __future__ import annotations

import pytest

from epipilot.core.models import (
    Evidence,
    EvidenceKind,
    Provenance,
    Task,
    new_evidence_id,
    new_task_id,
)
from epipilot.verification.pipeline import (
    CheckResult,
    VerificationRequest,
    VerifierPipeline,
)


class StubCheck:
    def __init__(self, result: CheckResult) -> None:
        self._result = result

    async def run(self, request: VerificationRequest) -> CheckResult:
        del request
        return self._result


def _evidence(kind: EvidenceKind, *, independently_verified: bool) -> Evidence:
    return Evidence(
        id=new_evidence_id(),
        kind=kind,
        summary="verification evidence",
        provenance=Provenance(source="test", scope="project/test"),
        independently_verified=independently_verified,
    )


@pytest.mark.asyncio
async def test_executor_report_alone_never_passes_pipeline() -> None:
    report = CheckResult(
        name="executor-report",
        passed=True,
        evidence=_evidence(EvidenceKind.EXECUTOR_REPORT, independently_verified=False),
    )
    pipeline = VerifierPipeline(checks=(StubCheck(report),))

    outcome = await pipeline.verify(
        VerificationRequest(task=Task(id=new_task_id(), objective="Implement feature"))
    )

    assert not outcome.passed
    with pytest.raises(ValueError, match="failed verification outcome"):
        outcome.completion_evidence()


@pytest.mark.asyncio
async def test_independent_deterministic_check_can_authorize_completion() -> None:
    deterministic = CheckResult(
        name="pytest",
        passed=True,
        evidence=_evidence(EvidenceKind.DETERMINISTIC_CHECK, independently_verified=True),
    )
    pipeline = VerifierPipeline(checks=(StubCheck(deterministic),))

    outcome = await pipeline.verify(
        VerificationRequest(task=Task(id=new_task_id(), objective="Implement feature"))
    )

    assert outcome.passed
    assert outcome.completion_evidence() == deterministic.evidence


@pytest.mark.asyncio
async def test_one_failed_check_prevents_successful_sibling_from_authorizing_completion() -> None:
    passed = CheckResult(
        name="unit-tests",
        passed=True,
        evidence=_evidence(EvidenceKind.DETERMINISTIC_CHECK, independently_verified=True),
    )
    failed = CheckResult(
        name="acceptance-test",
        passed=False,
        evidence=_evidence(EvidenceKind.DETERMINISTIC_CHECK, independently_verified=True),
    )
    pipeline = VerifierPipeline(checks=(StubCheck(passed), StubCheck(failed)))

    outcome = await pipeline.verify(
        VerificationRequest(task=Task(id=new_task_id(), objective="Implement feature"))
    )

    assert not outcome.passed
    with pytest.raises(ValueError, match="failed verification outcome"):
        outcome.completion_evidence()
