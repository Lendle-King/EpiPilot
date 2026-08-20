from datetime import UTC, datetime
from uuid import UUID

import pytest

from epipilot.core.events import EventType, ProjectEvent, new_event_id
from epipilot.events.codec import (
    EventPayloadTypeMismatch,
    MalformedEventPayload,
    UnsupportedEventSchema,
    decode_event_payload,
    encode_event_payload,
    make_project_event,
)
from epipilot.events.payloads import RequirementAddedPayload, TaskCreatedPayload
from epipilot.events.registry import TYPED_EVENT_SCHEMA_VERSION
from epipilot.requirements.models import RequirementKind

TASK_ID = UUID("00000000-0000-0000-0000-000000000101")
REQUIREMENT_ID = UUID("00000000-0000-0000-0000-000000000201")
CREATED_AT = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)


def test_event_codec_round_trip_is_deterministic() -> None:
    payload = TaskCreatedPayload(task_id=TASK_ID, objective="Build replay reducer")

    first = encode_event_payload(EventType.TASK_CREATED, payload)
    second = encode_event_payload(EventType.TASK_CREATED, payload)

    assert first == second
    event = make_project_event(EventType.TASK_CREATED, "project-1", payload)
    assert event.schema_version == TYPED_EVENT_SCHEMA_VERSION
    assert decode_event_payload(event) == payload


def test_event_codec_rejects_payload_type_mismatch() -> None:
    wrong_payload = RequirementAddedPayload(
        requirement_id=REQUIREMENT_ID,
        kind=RequirementKind.GOAL,
        statement="Ship deterministic replay",
        provenance_source="user",
        provenance_scope="project/project-1",
        provenance_created_at=CREATED_AT,
    )

    with pytest.raises(EventPayloadTypeMismatch):
        encode_event_payload(EventType.TASK_CREATED, wrong_payload)


@pytest.mark.parametrize("schema_version", [1, 999])
def test_unknown_or_legacy_schema_version_fails_closed(schema_version: int) -> None:
    payload = TaskCreatedPayload(task_id=TASK_ID, objective="Build replay reducer")
    encoded = encode_event_payload(EventType.TASK_CREATED, payload)
    event = ProjectEvent(
        id=new_event_id(),
        type=EventType.TASK_CREATED,
        aggregate_id="project-1",
        payload=encoded,
        schema_version=schema_version,
    )

    with pytest.raises(UnsupportedEventSchema):
        decode_event_payload(event)


def test_malformed_payload_fails_closed() -> None:
    event = ProjectEvent(
        id=new_event_id(),
        type=EventType.TASK_CREATED,
        aggregate_id="project-1",
        payload=b'{"task_id":"not-a-uuid","objective":"x","unexpected":true}',
        schema_version=TYPED_EVENT_SCHEMA_VERSION,
    )

    with pytest.raises(MalformedEventPayload):
        decode_event_payload(event)
