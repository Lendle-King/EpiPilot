from __future__ import annotations

import pytest

from epipilot.memory.models import (
    CanonicalMemoryRef,
    MemoryKind,
    MemoryScope,
    StructuralMemory,
    new_memory_id,
)
from epipilot.core.models import Provenance


def test_broad_memory_is_visible_from_child_scope() -> None:
    project = MemoryScope(project_id="p1")
    task = MemoryScope(
        project_id="p1",
        repository="repo",
        workstream="training",
        task_id="T-1",
    )

    assert project.visible_from(task)


def test_task_local_memory_does_not_leak_to_sibling_task() -> None:
    stored = MemoryScope(project_id="p1", repository="repo", task_id="T-1")
    sibling = MemoryScope(project_id="p1", repository="repo", task_id="T-2")

    assert not stored.visible_from(sibling)


def test_authoritative_memory_uses_reference_instead_of_freeform_copy() -> None:
    ref = CanonicalMemoryRef(
        id=new_memory_id(),
        kind=MemoryKind.SEMANTIC,
        entity_id="FACT-7",
        scope=MemoryScope(project_id="p1"),
    )

    assert ref.entity_id == "FACT-7"


def test_canonical_ref_rejects_episodic_kind() -> None:
    with pytest.raises(ValueError, match="authoritative ledgers"):
        CanonicalMemoryRef(
            id=new_memory_id(),
            kind=MemoryKind.EPISODIC,
            entity_id="episode-1",
            scope=MemoryScope(project_id="p1"),
        )


def test_structural_memory_requires_source_revision() -> None:
    with pytest.raises(ValueError, match="source revision"):
        StructuralMemory(
            id=new_memory_id(),
            scope=MemoryScope(project_id="p1", repository="repo"),
            source_revision="",
            content="repo map",
            provenance=Provenance(source="indexer", scope="p1/repo"),
        )
