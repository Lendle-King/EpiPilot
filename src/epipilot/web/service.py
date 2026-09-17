"""Join source metadata and canonical events into non-authoritative project views."""

from __future__ import annotations

import html
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from epipilot.web.events import load_stream
from epipilot.web.models import BackendView, Card, EventSummary, ProjectView, ReadModel, SourceRef
from epipilot.web.projection import build_project_view
from epipilot.web.repository import RepositoryInspector

if TYPE_CHECKING:
    from epipilot.state.project import ProjectState

BackendName = Literal["codex", "pi", "dsh"]
BACKEND_NAMES: tuple[BackendName, ...] = ("codex", "pi", "dsh")


class ExecutorPreference(ReadModel):
    backend: BackendName


class TaskProposal(ReadModel):
    status: Literal["proposal_only"] = "proposal_only"
    execution_authorized: Literal[False] = False
    project_id: str
    repository_revision: str
    event_version: int
    executor: ExecutorPreference
    objective: str
    rationale: str
    source_refs: tuple[SourceRef, ...]
    constraints: tuple[str, ...] = (
        "This is an unapproved handoff, not a canonical task.",
        "Define write scope, budget, and independent acceptance before execution.",
        "Revalidate the repository revision and project event version before use.",
    )


class SearchResponse(ReadModel):
    mode: Literal["source_retrieval_not_llm"] = "source_retrieval_not_llm"
    query: str
    results: tuple[Card, ...] = Field(default=())


def all_cards(view: ProjectView) -> tuple[Card, ...]:
    unique: dict[str, Card] = {}
    for card in (*view.goals, *view.requirements, *view.tasks, *view.knowledge,
                 *view.opportunities, *view.decisions, *view.evidence):
        unique[card.id] = card
    return tuple(unique.values())


@dataclass(slots=True)
class WorkbenchService:
    repository: Path
    events_db: Path | None = None
    project_id: str | None = None
    _inspector: RepositoryInspector = field(init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    def __post_init__(self) -> None:
        if self.events_db is not None and not self.project_id:
            raise ValueError("--project-id is required with --events-db")
        self._inspector = RepositoryInspector(self.repository)

    def snapshot(self) -> ProjectView:
        with self._lock:
            repo = self._inspector.inspect()
            state: ProjectState | None = None
            changes: tuple[EventSummary, ...] = ()
            project_id = self.project_id or f"repository:{repo.name}"
            if self.events_db is not None:
                state, changes = load_stream(self.events_db, project_id)
            view = build_project_view(
                state, repo, project_id=project_id,
                captured_at=datetime.now(UTC).isoformat(),
            )
            return view.model_copy(update={
                "changes": changes,
                "backends": tuple(BackendView(
                    name=name, executable_available=shutil.which(name) is not None,
                ) for name in BACKEND_NAMES),
            })

    def search(self, query: str) -> SearchResponse:
        view = self.snapshot()
        terms = query.casefold().split()
        results = tuple(card for card in all_cards(view) if all(
            term in " ".join((card.title, card.summary, *card.details.values())).casefold()
            for term in terms
        ))
        return SearchResponse(query=query, results=results[:30])

    def proposal(self, card_id: str, backend: BackendName) -> TaskProposal:
        view = self.snapshot()
        for card in (*view.opportunities, *view.tasks):
            if card.id == card_id:
                return TaskProposal(
                    project_id=view.project_id, repository_revision=view.repository.revision,
                    event_version=view.event_version, executor=ExecutorPreference(backend=backend),
                    objective=card.title, rationale=card.summary, source_refs=card.refs,
                )
        raise KeyError(card_id)

    def report(self) -> str:
        view = self.snapshot()
        lines = [f"# {html.escape(view.name)} — 项目简报", "",
                 f"Git revision: `{view.repository.revision}`",
                 f"Event version: {view.event_version}",
                 f"Captured at: {view.captured_at}",
                 "Project acceptance: **NOT ASSESSED**", "",
                 "This is a read-model export, not proof of completion or authorization to execute.",
                 "Historical verification does not establish applicability to the current Git HEAD.", ""]
        sections = (("当前目标", view.goals), ("验收与约束", view.requirements),
                    ("项目认知", view.knowledge), ("目标推进", view.tasks),
                    ("待调查的优化机会", view.opportunities), ("证据", view.evidence))
        for title, cards in sections:
            lines.extend((f"## {title}", ""))
            if not cards:
                lines.append("尚未提供数据。")
            for card in cards:
                lines.extend((f"### {html.escape(card.title)}", f"State: {card.status}",
                              html.escape(card.summary)))
                for ref in card.refs:
                    lines.append(f"Source: {html.escape(ref.location)}; "
                                 f"revision: {ref.revision or 'not linked'}; "
                                 f"scope: {html.escape(ref.scope or 'not specified')}")
                lines.append("")
        return "\n".join(lines) + "\n"
