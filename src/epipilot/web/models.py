"""Versioned, non-authoritative read models for the project workbench."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReadModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceRef(ReadModel):
    kind: str
    location: str
    revision: str | None = None
    scope: str | None = None
    recorded_at: str | None = None


class Card(ReadModel):
    id: str
    title: str
    status: str
    summary: str = ""
    details: dict[str, str] = Field(default_factory=dict)
    refs: tuple[SourceRef, ...] = ()
    related_ids: tuple[str, ...] = ()


class DocumentView(ReadModel):
    path: str
    text: str
    truncated: bool = False
    revision: str


class RepositoryView(ReadModel):
    name: str
    revision: str
    branch: str
    dirty: bool
    total_files: int
    scanned_files: int
    truncated: bool
    documents: tuple[DocumentView, ...] = ()
    modules: tuple[Card, ...] = ()
    warnings: tuple[str, ...] = ()


class EventSummary(ReadModel):
    id: str
    version: int
    type: str
    occurred_at: str


class BackendView(ReadModel):
    name: str
    executable_available: bool
    usage: str = "proposal_export_only"
    execution_enabled: bool = False
    resume_enabled: bool = False


class ProjectView(ReadModel):
    schema_version: int = 1
    project_id: str
    name: str
    source_mode: Literal["repository", "events"]
    captured_at: str
    event_version: int = 0
    repository: RepositoryView
    summary: str
    goals: tuple[Card, ...] = ()
    requirements: tuple[Card, ...] = ()
    tasks: tuple[Card, ...] = ()
    knowledge: tuple[Card, ...] = ()
    opportunities: tuple[Card, ...] = ()
    decisions: tuple[Card, ...] = ()
    evidence: tuple[Card, ...] = ()
    next_steps: tuple[Card, ...] = ()
    changes: tuple[EventSummary, ...] = ()
    backends: tuple[BackendView, ...] = ()
    verified_task_count: int = 0
    acceptance_status: Literal["not_assessed"] = "not_assessed"
    execution_enabled: bool = False
    warnings: tuple[str, ...] = ()
