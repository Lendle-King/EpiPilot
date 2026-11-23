"""Safe checkpoint serialization, integrity validation, and state restoration."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime

from pydantic import TypeAdapter, ValidationError

from epipilot.checkpoint.errors import (
    CheckpointChecksumMismatch,
    CheckpointStateInvalid,
    UnsupportedCheckpointSchema,
)
from epipilot.checkpoint.models import CHECKPOINT_SCHEMA_VERSION, Checkpoint
from epipilot.core.models import utc_now
from epipilot.state.project import ProjectState

_STATE_ADAPTER: TypeAdapter[ProjectState] = TypeAdapter(ProjectState)


def serialize_project_state(state: ProjectState) -> bytes:
    """Serialize immutable canonical state without executable object formats."""
    return _STATE_ADAPTER.dump_json(state)


def deserialize_project_state(data: bytes) -> ProjectState:
    """Decode checkpoint state through the typed ProjectState schema."""
    try:
        return _STATE_ADAPTER.validate_json(data)
    except (ValidationError, ValueError) as exc:
        raise CheckpointStateInvalid("checkpoint state payload is malformed") from exc


def create_checkpoint(
    state: ProjectState,
    *,
    created_at: datetime | None = None,
) -> Checkpoint:
    """Create a checksummed snapshot from already-replayed canonical state."""
    timestamp = created_at or utc_now()
    serialized = serialize_project_state(state)
    checksum = _checkpoint_checksum(
        project_id=state.project_id,
        last_event_version=state.event_version,
        serialized_project_state=serialized,
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        created_at=timestamp,
    )
    return Checkpoint(
        project_id=state.project_id,
        last_event_version=state.event_version,
        serialized_project_state=serialized,
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        checksum=checksum,
        created_at=timestamp,
    )


def load_checkpoint(checkpoint: Checkpoint) -> ProjectState:
    """Validate a snapshot and return its decoded state, failing closed."""
    if checkpoint.schema_version != CHECKPOINT_SCHEMA_VERSION:
        raise UnsupportedCheckpointSchema(
            f"unsupported checkpoint schema {checkpoint.schema_version}; "
            f"expected {CHECKPOINT_SCHEMA_VERSION}"
        )

    expected_checksum = _checkpoint_checksum(
        project_id=checkpoint.project_id,
        last_event_version=checkpoint.last_event_version,
        serialized_project_state=checkpoint.serialized_project_state,
        schema_version=checkpoint.schema_version,
        created_at=checkpoint.created_at,
    )
    if not hmac.compare_digest(checkpoint.checksum, expected_checksum):
        raise CheckpointChecksumMismatch("checkpoint checksum validation failed")

    state = deserialize_project_state(checkpoint.serialized_project_state)
    if state.project_id != checkpoint.project_id:
        raise CheckpointStateInvalid("checkpoint state project_id does not match envelope")
    if state.event_version != checkpoint.last_event_version:
        raise CheckpointStateInvalid("checkpoint state event version does not match envelope")
    return state


def _checkpoint_checksum(
    *,
    project_id: str,
    last_event_version: int,
    serialized_project_state: bytes,
    schema_version: int,
    created_at: datetime,
) -> str:
    metadata = json.dumps(
        {
            "created_at": created_at.isoformat(),
            "last_event_version": last_event_version,
            "project_id": project_id,
            "schema_version": schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(metadata + b"\n" + serialized_project_state).hexdigest()
