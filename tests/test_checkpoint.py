from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from epipilot.checkpoint.codec import create_checkpoint, load_checkpoint
from epipilot.checkpoint.errors import CheckpointChecksumMismatch, UnsupportedCheckpointSchema
from epipilot.checkpoint.sqlite_store import SqliteCheckpointStore
from epipilot.core.models import Provenance, RequirementId, Task, TaskId
from epipilot.planning.graph import PlanBasis, PlanBasisKind, PlanGraph
from epipilot.requirements.models import Requirement, RequirementKind
from epipilot.state.project import ProjectState

CREATED_AT = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
TASK_ID = TaskId(UUID("00000000-0000-0000-0000-000000000401"))
REQUIREMENT_ID = RequirementId(UUID("00000000-0000-0000-0000-000000000402"))


def _state(*, event_version: int = 3) -> ProjectState:
    requirement = Requirement(
        id=REQUIREMENT_ID,
        kind=RequirementKind.GOAL,
        statement="Recover deterministically after interruption",
        provenance=Provenance(
            source="test",
            scope="project/checkpoint",
            created_at=CREATED_AT,
        ),
    )
    task = Task(id=TASK_ID, objective="Checkpoint the project")
    plan = PlanGraph(
        version=1,
        tasks=(task,),
        dependencies=(),
        basis=(PlanBasis(PlanBasisKind.REQUIREMENT, str(REQUIREMENT_ID)),),
    )
    return ProjectState(
        project_id="project-checkpoint",
        requirements=(requirement,),
        tasks=(task,),
        plans=(plan,),
        event_version=event_version,
    )


def test_checkpoint_round_trip_preserves_typed_project_state() -> None:
    state = _state()

    checkpoint = create_checkpoint(state, created_at=CREATED_AT)
    restored = load_checkpoint(checkpoint)

    assert restored == state
    assert checkpoint.last_event_version == state.event_version
    assert len(checkpoint.checksum) == 64


def test_corrupted_checkpoint_checksum_fails_closed() -> None:
    checkpoint = create_checkpoint(_state(), created_at=CREATED_AT)
    corrupted = replace(
        checkpoint,
        serialized_project_state=checkpoint.serialized_project_state + b" ",
    )

    with pytest.raises(CheckpointChecksumMismatch):
        load_checkpoint(corrupted)


def test_unknown_checkpoint_schema_fails_closed() -> None:
    checkpoint = create_checkpoint(_state(), created_at=CREATED_AT)
    unsupported = replace(checkpoint, schema_version=99)

    with pytest.raises(UnsupportedCheckpointSchema):
        load_checkpoint(unsupported)


def test_sqlite_checkpoint_store_survives_reopen_and_selects_latest(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.sqlite"
    first = create_checkpoint(_state(event_version=3), created_at=CREATED_AT)
    second = create_checkpoint(_state(event_version=5), created_at=CREATED_AT)
    store = SqliteCheckpointStore(path)
    store.save(first)
    store.save(second)

    reopened = SqliteCheckpointStore(path)
    latest = reopened.latest("project-checkpoint")

    assert latest == second
    assert latest is not None
    assert load_checkpoint(latest) == _state(event_version=5)
