"""Immutable checkpoint envelope for crash-safe project recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

CHECKPOINT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Validated snapshot envelope.

    The event stream remains canonical. This envelope is only a recovery optimization.
    """

    project_id: str
    last_event_version: int
    serialized_project_state: bytes
    schema_version: int
    checksum: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("checkpoint project_id must not be empty")
        if self.last_event_version < 0:
            raise ValueError("checkpoint event version must not be negative")
        if not self.serialized_project_state:
            raise ValueError("checkpoint state bytes must not be empty")
        if self.schema_version < 1:
            raise ValueError("checkpoint schema version must be positive")
        if len(self.checksum) != 64 or any(
            character not in "0123456789abcdef" for character in self.checksum
        ):
            raise ValueError("checkpoint checksum must be a lowercase SHA-256 digest")
        if self.created_at.tzinfo is None:
            raise ValueError("checkpoint timestamp must be timezone-aware")
