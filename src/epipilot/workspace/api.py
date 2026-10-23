"""Loopback-only API for the local research workspace.

This API stages projects and historical notes; it cannot promote EpiPilot
canonical evidence, execute remote GitHub/Notion actions, or place trades.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from subprocess import TimeoutExpired
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from epipilot.workspace.onboarding import WorkspaceStore
from epipilot.workspace.remote_model import ThirdPartyChatModel, model_configuration
from epipilot.workspace.research_agent import ResearchOnboardingAgent, experience_digest

app = FastAPI(title="EpiPilot Research Workspace", version="0.1.0")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=3000)
    mode: Literal["create", "adopt"]
    repository_path: str | None = None


class ReviewInput(BaseModel):
    decision: Literal["accepted_note", "rejected"]


class IntentInput(BaseModel):
    message: str = Field(min_length=1, max_length=3000)


def _store() -> WorkspaceStore:
    path = Path(
        os.environ.get(
            "EPIPILOT_WORKSPACE_DB",
            str(Path.home() / ".local" / "share" / "epipilot" / "workspace.sqlite3"),
        )
    ).expanduser()
    roots = os.environ.get("EPIPILOT_ALLOWED_ROOTS")
    allowed = tuple(Path(v) for v in roots.split(os.pathsep) if v) if roots else None
    return WorkspaceStore(path, roots=allowed)


def _raise(error: Exception) -> None:
    if isinstance(error, KeyError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "local-only", "remote_actions": "disabled"}


@app.get("/api/projects")
def list_projects() -> list[dict[str, object]]:
    return _store().projects()


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectInput) -> dict[str, object]:
    try:
        return _store().create(body.name, body.goal, body.mode, body.repository_path)
    except (ValueError, OSError, TimeoutExpired) as error:
        _raise(error)
    return {}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, object]:
    try:
        return _store().get(project_id)
    except (KeyError, ValueError, OSError) as error:
        _raise(error)
    return {}


@app.get("/api/projects/{project_id}/git")
def get_git(project_id: str) -> dict[str, object]:
    try:
        return _store().git_status(project_id)
    except (KeyError, ValueError, OSError) as error:
        _raise(error)
    return {}


@app.post("/api/projects/{project_id}/candidates/{candidate_id}/review")
def review_candidate(project_id: str, candidate_id: str, body: ReviewInput) -> dict[str, object]:
    try:
        return _store().review(project_id, candidate_id, body.decision)
    except (KeyError, ValueError) as error:
        _raise(error)
    return {}


@app.post("/api/projects/{project_id}/intent")
def propose_intent(project_id: str, body: IntentInput) -> dict[str, object]:
    try:
        return _store().intent(project_id, body.message)
    except (KeyError, ValueError) as error:
        _raise(error)
    return {}


@app.get("/api/projects/{project_id}/chat-prompt")
def get_chat_prompt(project_id: str) -> dict[str, str]:
    try:
        return {"prompt": _store().chat_prompt(project_id)}
    except (KeyError, ValueError) as error:
        _raise(error)
    return {}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787)


if __name__ == "__main__":
    main()


class SemanticRunInput(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=160)
    max_documents: int = Field(default=4, ge=1, le=12)
    # Explicit human consent is needed before any research excerpts leave this machine.
    consent: Literal[True]


class SemanticImportInput(BaseModel):
    source_path: str = Field(min_length=1, max_length=500)
    response_json: str = Field(min_length=2, max_length=70_000)


class SemanticReviewInput(BaseModel):
    decision: Literal["accepted_note", "rejected"]


@app.get("/api/research-model/config")
def research_model_config() -> dict[str, object]:
    """Only non-secret configuration crosses the local HTTP boundary."""
    return model_configuration()


@app.get("/api/projects/{project_id}/research-agent")
def research_report(project_id: str) -> dict[str, object]:
    try:
        return ResearchOnboardingAgent(_store()).report(project_id)
    except (KeyError, ValueError, OSError) as error:
        _raise(error)
    return {}


@app.get("/api/projects/{project_id}/research-agent/digest")
def research_digest(project_id: str) -> dict[str, object]:
    try:
        return experience_digest(ResearchOnboardingAgent(_store()).report(project_id))
    except (KeyError, ValueError, OSError) as error:
        _raise(error)
    return {}


@app.get("/api/projects/{project_id}/research-agent/chat-packet")
def research_chat_packet(project_id: str, max_documents: int = 2) -> dict[str, object]:
    try:
        return ResearchOnboardingAgent(_store()).chat_packet(project_id, max_documents)
    except (KeyError, ValueError, OSError) as error:
        _raise(error)
    return {}


@app.post("/api/projects/{project_id}/research-agent/run")
async def run_research_agent(project_id: str, body: SemanticRunInput) -> dict[str, object]:
    """Human opt-in API call. The model returns only reviewable, sourced candidates."""
    try:
        model = ThirdPartyChatModel.from_environment(body.model)
        provider = model_configuration()["provider_host"]
        return await asyncio.to_thread(
            ResearchOnboardingAgent(_store()).run_model,
            project_id,
            model,
            f"third_party:{provider}:{model.model}",
            body.max_documents,
        )
    except (KeyError, ValueError, OSError, RuntimeError, TimeoutExpired) as error:
        _raise(error)
    return {}


@app.post("/api/projects/{project_id}/research-agent/chat-import")
def import_research_chat(project_id: str, body: SemanticImportInput) -> dict[str, object]:
    try:
        return ResearchOnboardingAgent(_store()).import_chat_batch(
            project_id, body.source_path, body.response_json
        )
    except (KeyError, ValueError, OSError) as error:
        _raise(error)
    return {}


@app.post("/api/projects/{project_id}/research-agent/claims/{claim_id}/review")
def review_research_claim(
    project_id: str, claim_id: str, body: SemanticReviewInput
) -> dict[str, object]:
    try:
        return ResearchOnboardingAgent(_store()).review(project_id, claim_id, body.decision)
    except (KeyError, ValueError, OSError) as error:
        _raise(error)
    return {}
