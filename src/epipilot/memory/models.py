"""Typed long-term memory models.

Canonical requirements, facts, hypotheses, and unknowns are not copied into free-form
memory. They remain authoritative in their own ledgers and are referenced by identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType
from uuid import UUID, uuid4

from epipilot.core.models import Provenance

MemoryId = NewType("MemoryId", UUID)


def new_memory_id() -> MemoryId:
    return MemoryId(uuid4())


class MemoryKind(StrEnum):
    """Logical memory classes used by retrieval and context compilation."""

    NORMATIVE = "normative"
    SEMANTIC = "semantic"
    EPISTEMIC = "epistemic"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"
    STRUCTURAL = "structural"


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """Hierarchical visibility scope for one memory item."""

    project_id: str
    repository: str | None = None
    workstream: str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("project_id", self.project_id),
            ("repository", self.repository),
            ("workstream", self.workstream),
            ("task_id", self.task_id),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be non-empty when provided")

    def visible_from(self, current: MemoryScope) -> bool:
        """Return whether this item is in scope for ``current``.

        A broader item is visible from a more specific child scope, but a task-local item
        never leaks into a sibling task or a broader repository/project context.
        """
        if self.project_id != current.project_id:
            return False
        for stored, requested in (
            (self.repository, current.repository),
            (self.workstream, current.workstream),
            (self.task_id, current.task_id),
        ):
            if stored is not None and stored != requested:
                return False
        return True


@dataclass(frozen=True, slots=True)
class CanonicalMemoryRef:
    """Reference to authoritative state instead of a stale textual copy."""

    id: MemoryId
    kind: MemoryKind
    entity_id: str
    scope: MemoryScope

    def __post_init__(self) -> None:
        if self.kind not in {
            MemoryKind.NORMATIVE,
            MemoryKind.SEMANTIC,
            MemoryKind.EPISTEMIC,
        }:
            raise ValueError("canonical memory refs are only for authoritative ledgers")
        if not self.entity_id.strip():
            raise ValueError("canonical memory entity id must not be empty")


@dataclass(frozen=True, slots=True)
class EpisodicMemory:
    """Consolidated high-value experience, not a raw conversation transcript."""

    id: MemoryId
    scope: MemoryScope
    problem: str
    attempt: str
    outcome: str
    lesson: str
    provenance: Provenance

    def __post_init__(self) -> None:
        for name, value in (
            ("problem", self.problem),
            ("attempt", self.attempt),
            ("outcome", self.outcome),
            ("lesson", self.lesson),
        ):
            if not value.strip():
                raise ValueError(f"episodic {name} must not be empty")


@dataclass(frozen=True, slots=True)
class ProceduralMemory:
    """Reusable playbook activated by an explicit trigger and scope."""

    id: MemoryId
    scope: MemoryScope
    trigger: str
    procedure: str
    provenance: Provenance
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.trigger.strip():
            raise ValueError("procedural trigger must not be empty")
        if not self.procedure.strip():
            raise ValueError("procedure must not be empty")


@dataclass(frozen=True, slots=True)
class StructuralMemory:
    """Generated repository/system map pinned to a source revision."""

    id: MemoryId
    scope: MemoryScope
    source_revision: str
    content: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if not self.source_revision.strip():
            raise ValueError("structural memory must be pinned to a source revision")
        if not self.content.strip():
            raise ValueError("structural memory content must not be empty")
