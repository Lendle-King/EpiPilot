from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from epipilot.core.events import EventType
from epipilot.events.codec import make_project_event
from epipilot.events.payloads import RequirementAddedPayload
from epipilot.requirements.models import RequirementKind
from epipilot.runtime.sqlite_event_store import SqliteEventStore
from epipilot.web.app import create_app
from epipilot.web.events import load_stream
from epipilot.web.service import WorkbenchService
from test_web_repository import make_repo


def seed(path: Path) -> None:
    event = make_project_event(
        EventType.REQUIREMENT_ADDED,
        "p",
        RequirementAddedPayload(
            requirement_id=uuid4(),
            kind=RequirementKind.GOAL,
            statement="A real replayed goal",
            provenance_source="synthetic test",
            provenance_scope="fixture",
            provenance_created_at=datetime(2026, 9, 17, tzinfo=UTC),
        ),
    )
    SqliteEventStore(path).append(event, expected_version=0)


def test_real_sqlite_replay_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite"
    seed(path)
    before = path.read_bytes()
    state, events = load_stream(path, "p")
    assert state.requirements[0].statement == "A real replayed goal"
    assert state.event_version == 1
    assert events[0].version == 1
    assert path.read_bytes() == before


def test_event_backed_http_view_uses_the_canonical_kernel(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite"
    seed(path)
    service = WorkbenchService(make_repo(tmp_path / "repo"), events_db=path, project_id="p")
    client = TestClient(create_app(service), base_url="http://127.0.0.1")
    response = client.get("/api/project")
    assert response.status_code == 200
    assert response.json()["goals"][0]["title"] == "A real replayed goal"
    assert response.json()["source_mode"] == "events"


def test_unknown_aggregate_is_not_empty_success(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite"
    seed(path)
    with pytest.raises(ValueError, match="not found"):
        load_stream(path, "absent")


def test_version_gap_is_rejected_before_replay(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite"
    seed(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE project_events SET version = 2")
        connection.commit()
    with pytest.raises(ValueError, match="gap"):
        load_stream(path, "p")


def test_bad_schema_cannot_be_silently_displayed(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite"
    seed(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE project_events SET schema_version = 99")
        connection.commit()
    with pytest.raises(ValueError):
        load_stream(path, "p")
