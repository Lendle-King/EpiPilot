"""Loopback-only API for the local research workspace.

This API stages projects and historical notes; it cannot promote EpiPilot
canonical evidence, execute remote GitHub/Notion actions, or place trades.
"""

from __future__ import annotations

import os
from pathlib import Path
from subprocess import TimeoutExpired
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from epipilot.workspace.onboarding import WorkspaceStore

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
