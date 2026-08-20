"""Deterministic encoding and strict decoding for typed project events."""

from __future__ import annotations

import json

from pydantic import ValidationError

from epipilot.core.events import EventType, ProjectEvent, new_event_id
from epipilot.events.payloads import EventPayload
from epipilot.events.registry import EVENT_PAYLOAD_TYPES, TYPED_EVENT_SCHEMA_VERSION


class EventCodecError(ValueError):
    """Base error for typed event payload failures."""


class UnsupportedEventSchema(EventCodecError):
    """Raised when replay encounters a schema version it does not understand."""


class EventPayloadTypeMismatch(EventCodecError):
    """Raised when an event kind is paired with the wrong typed payload."""


class MalformedEventPayload(EventCodecError):
    """Raised when bytes cannot be decoded into the registered strict schema."""


def encode_event_payload(event_type: EventType, payload: EventPayload) -> bytes:
    """Encode one registered payload as deterministic UTF-8 JSON."""
    expected = EVENT_PAYLOAD_TYPES[event_type]
    if not isinstance(payload, expected):
        raise EventPayloadTypeMismatch(
            f"{event_type.value} requires {expected.__name__}, got {type(payload).__name__}"
        )
    data = payload.model_dump(mode="json")
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def decode_event_payload(event: ProjectEvent) -> EventPayload:
    """Decode an event payload, failing closed on version or schema mismatch."""
    if event.schema_version != TYPED_EVENT_SCHEMA_VERSION:
        raise UnsupportedEventSchema(
            f"unsupported event schema version {event.schema_version}; "
            f"expected {TYPED_EVENT_SCHEMA_VERSION}"
        )

    payload_type = EVENT_PAYLOAD_TYPES[event.type]
    try:
        return payload_type.model_validate_json(event.payload)
    except (ValidationError, ValueError) as exc:
        raise MalformedEventPayload(
            f"malformed payload for {event.type.value} schema {event.schema_version}"
        ) from exc


def make_project_event(
    event_type: EventType,
    aggregate_id: str,
    payload: EventPayload,
) -> ProjectEvent:
    """Create a typed replayable event using the current schema version."""
    if not aggregate_id.strip():
        raise ValueError("aggregate_id must not be empty")
    return ProjectEvent(
        id=new_event_id(),
        type=event_type,
        aggregate_id=aggregate_id,
        payload=encode_event_payload(event_type, payload),
        schema_version=TYPED_EVENT_SCHEMA_VERSION,
    )
