"""Checkpoint-store port and deterministic in-memory implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from epipilot.checkpoint.models import Checkpoint


class CheckpointStore(Protocol):
    """Persistence boundary for non-canonical recovery snapshots."""

    def save(self, checkpoint: Checkpoint) -> None:
        """Persist or replace a snapshot for one aggregate event version."""
        ...

    def latest(self, project_id: str) -> Checkpoint | None:
        """Return the highest-version checkpoint for a project, if any."""
        ...

    def discard_latest(self, project_id: str) -> None:
        """Discard the newest snapshot after recovery determines it is unusable."""
        ...


@dataclass(slots=True)
class InMemoryCheckpointStore:
    """Reference checkpoint store for tests and single-process use."""

    _checkpoints: dict[str, dict[int, Checkpoint]] = field(default_factory=dict)

    def save(self, checkpoint: Checkpoint) -> None:
        versions = self._checkpoints.setdefault(checkpoint.project_id, {})
        versions[checkpoint.last_event_version] = checkpoint

    def latest(self, project_id: str) -> Checkpoint | None:
        versions = self._checkpoints.get(project_id)
        if not versions:
            return None
        return versions[max(versions)]

    def discard_latest(self, project_id: str) -> None:
        versions = self._checkpoints.get(project_id)
        if not versions:
            return
        del versions[max(versions)]
        if not versions:
            del self._checkpoints[project_id]
