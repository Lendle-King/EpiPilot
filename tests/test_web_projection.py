from __future__ import annotations

from datetime import UTC, datetime

import pytest

from epipilot.core.models import Evidence, EvidenceKind, Provenance, Task, TaskStatus
from epipilot.core.models import new_evidence_id, new_task_id
from epipilot.state.project import ProjectState, VerificationRecord
from epipilot.web.models import RepositoryView
from epipilot.web.projection import build_project_view


def repository() -> RepositoryView:
    return RepositoryView(name="Example", revision="a" * 40, branch="main", dirty=False,
                          total_files=0, scanned_files=0, truncated=False)


def test_reported_done_never_counts_as_verified() -> None:
    state = ProjectState(project_id="p", tasks=(Task(
        id=new_task_id(), objective="Fix", status=TaskStatus.AGENT_REPORTED_DONE,
    ),))
    view = build_project_view(state, repository(), project_id="p", captured_at="2026-09-17")
    assert view.verified_task_count == 0
    assert view.tasks[0].status == "agent_reported_done"
    assert view.acceptance_status == "not_assessed"
    assert state.tasks[0].status is TaskStatus.AGENT_REPORTED_DONE


@pytest.mark.parametrize("independent", [True, False])
def test_task_verification_is_not_project_acceptance(independent: bool) -> None:
    proof = Evidence(id=new_evidence_id(), kind=EvidenceKind.DETERMINISTIC_CHECK,
                     summary="Synthetic check", independently_verified=independent,
                     provenance=Provenance(source="test", scope="synthetic fixture",
                                           created_at=datetime(2026, 9, 17, tzinfo=UTC)))
    task = Task(id=new_task_id(), objective="Fix", status=TaskStatus.PASSED,
                linked_evidence=(proof.id,))
    state = ProjectState(project_id="p", tasks=(task,), evidence=(proof,),
                         verifications=(VerificationRecord(task_id=task.id, passed=True,
                                                           evidence_id=proof.id),))
    view = build_project_view(state, repository(), project_id="p", captured_at="2026-09-17")
    assert view.verified_task_count == int(independent)
    assert view.acceptance_status == "not_assessed"
    assert view.evidence[0].refs[0].revision is None
    assert view.execution_enabled is False
    if not independent:
        assert view.tasks[0].status == "needs_review"


def test_missing_verification_record_fails_closed() -> None:
    task = Task(id=new_task_id(), objective="Claimed fix", status=TaskStatus.PASSED,
                linked_evidence=(new_evidence_id(),))
    view = build_project_view(ProjectState(project_id="p", tasks=(task,)), repository(),
                              project_id="p", captured_at="2026-09-17")
    assert view.verified_task_count == 0
    assert view.tasks[0].status == "needs_review"


def test_projection_rejects_wrong_project_id() -> None:
    with pytest.raises(ValueError, match="project id"):
        build_project_view(ProjectState(project_id="other"), repository(),
                           project_id="p", captured_at="2026-09-17")
