"""Loopback-only API for the local research workspace.

This API stages projects and historical notes; it cannot promote EpiPilot
canonical evidence, execute remote GitHub/Notion actions, or place trades.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from subprocess import TimeoutExpired
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from epipilot.workspace.chatgpt_web import ChatGPTWebSettings, PlaywrightChatGPTDriver
from epipilot.workspace.experiment_routes import ExperimentRoutePlanner
from epipilot.workspace.iteration_loop import AutoIterationLoop
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


def _allowed_roots() -> tuple[Path, ...]:
    """Resolved adoption roots; empty only when explicitly configured to nothing."""
    raw = os.environ.get("EPIPILOT_ALLOWED_ROOTS", "")
    if not raw:
        return (Path.home().resolve(),)
    return tuple(
        Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part.strip()
    )


def _within_roots(target: Path, roots: tuple[Path, ...]) -> bool:
    return any(target == root or root in target.parents for root in roots)


def _store() -> WorkspaceStore:
    path = Path(
        os.environ.get(
            "EPIPILOT_WORKSPACE_DB",
            str(Path.home() / ".local" / "share" / "epipilot" / "workspace.sqlite3"),
        )
    ).expanduser()
    return WorkspaceStore(path, roots=_allowed_roots())


def _raise(error: Exception) -> None:
    if isinstance(error, KeyError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "local-only", "remote_actions": "disabled"}


_MAX_FS_ENTRIES = 1000


@app.get("/api/fs/list")
def list_directories(path: str | None = None) -> dict[str, object]:
    """Browse directories inside the configured adoption roots (fail closed).

    Hidden entries are omitted; symlinked entries resolving outside the roots
    are skipped; a requested path resolving outside the roots is rejected.
    """
    roots = _allowed_roots()
    if not roots:
        raise HTTPException(status_code=400, detail="No allowed roots are configured")
    target = Path(path).expanduser() if path else roots[0]
    try:
        resolved = target.resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Directory does not exist") from None
    except OSError:
        raise HTTPException(status_code=400, detail="Path cannot be resolved") from None
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")
    if not _within_roots(resolved, roots):
        raise HTTPException(status_code=400, detail="Path is outside configured allowed roots")

    try:
        children = sorted(
            (child for child in resolved.iterdir() if not child.name.startswith(".")),
            key=lambda child: child.name.casefold(),
        )
    except OSError:
        raise HTTPException(status_code=400, detail="Directory cannot be read") from None

    entries: list[dict[str, object]] = []
    truncated = False
    for child in children:
        if not child.is_dir():
            continue
        try:
            child_resolved = child.resolve(strict=True)
        except OSError:
            continue
        if not _within_roots(child_resolved, roots):
            continue
        if len(entries) >= _MAX_FS_ENTRIES:
            truncated = True
            break
        entries.append(
            {
                "name": child.name,
                "path": str(child_resolved),
                "is_git": (child_resolved / ".git").exists(),
            }
        )
    parent = None if any(resolved == root for root in roots) else str(resolved.parent)
    return {
        "path": str(resolved),
        "parent": parent,
        "roots": [str(root) for root in roots],
        "entries": entries,
        "truncated": truncated,
    }


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


class AgentRunInput(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=160)
    max_documents: int = Field(default=4, ge=1, le=12)


class ChatImportInput(BaseModel):
    source_path: str = Field(min_length=1, max_length=500)
    response_json: str = Field(min_length=2, max_length=70000)


class SemanticReviewInput(BaseModel):
    decision: Literal["accepted_note", "rejected"]


class RouteRunInput(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=160)


class RouteReviewInput(BaseModel):
    review_json: str = Field(min_length=2, max_length=70000)


class LoopInput(BaseModel):
    max_rounds: int = Field(default=3, ge=1, le=12)


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
        agent = ResearchOnboardingAgent(_store())
        return experience_digest(agent.report(project_id))
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


@app.get("/api/research-model/config")
def research_model_config() -> dict[str, object]:
    return model_configuration()


@app.post("/api/projects/{project_id}/research-agent/run")
async def run_research_agent(project_id: str, body: AgentRunInput) -> dict[str, object]:
    try:
        agent = ResearchOnboardingAgent(_store())
        model = ThirdPartyChatModel.from_environment(body.model)
        return await asyncio.to_thread(
            agent.run_model,
            project_id,
            model,
            "third_party:" + str(model_configuration()["provider_host"]) + ":" + model.model,
            body.max_documents,
        )
    except (KeyError, ValueError, OSError, RuntimeError, TimeoutExpired) as error:
        _raise(error)
    return {}


@app.post("/api/projects/{project_id}/research-agent/chat-import")
def import_research_chat(project_id: str, body: ChatImportInput) -> dict[str, object]:
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


@app.get("/api/projects/{project_id}/research-agent/route")
def get_research_route(project_id: str) -> dict[str, object]:
    try:
        return ExperimentRoutePlanner(_store()).report(project_id)
    except (KeyError, ValueError, OSError) as error:
        _raise(error)
    return {}


@app.post("/api/projects/{project_id}/research-agent/route")
async def generate_research_route(project_id: str, body: RouteRunInput) -> dict[str, object]:
    try:
        planner = ExperimentRoutePlanner(_store())
        model = ThirdPartyChatModel.from_environment(body.model)
        return await asyncio.to_thread(
            planner.generate,
            project_id,
            model,
            "third_party:" + str(model_configuration()["provider_host"]) + ":" + model.model,
        )
    except (KeyError, ValueError, OSError, RuntimeError, TimeoutExpired) as error:
        _raise(error)
    return {}


@app.post("/api/projects/{project_id}/research-agent/route/review")
def import_research_route_review(project_id: str, body: RouteReviewInput) -> dict[str, object]:
    try:
        return ExperimentRoutePlanner(_store()).import_review(project_id, body.review_json)
    except (KeyError, ValueError, OSError) as error:
        _raise(error)
    return {}


@app.post("/api/projects/{project_id}/research-agent/route/chatgpt")
async def drive_chatgpt_iteration(project_id: str) -> dict[str, object]:
    try:
        return await asyncio.to_thread(_drive_chatgpt_sync, project_id)
    except (KeyError, ValueError, OSError, RuntimeError, TimeoutExpired) as error:
        _raise(error)
    return {}


_MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _chatgpt_settings() -> ChatGPTWebSettings:
    executable = os.environ.get("EPIPILOT_CHATGPT_BROWSER") or (
        _MAC_CHROME if Path(_MAC_CHROME).exists() else None
    )
    return ChatGPTWebSettings(
        profile_dir=Path(
            os.environ.get(
                "EPIPILOT_CHATGPT_PROFILE",
                str(Path.home() / ".local" / "share" / "epipilot" / "chatgpt-profile"),
            )
        ).expanduser(),
        browser_executable=executable,
    )


def _drive_chatgpt_sync(project_id: str) -> dict[str, object]:
    planner = ExperimentRoutePlanner(_store())
    item = _store().get(project_id)
    settings = _chatgpt_settings()
    return planner.run_chatgpt_iteration(
        project_id,
        PlaywrightChatGPTDriver(settings),
        str(item.get("name", "project")),
    )


_auto_iteration = AutoIterationLoop()


def _run_auto_iteration_sync(
    project_id: str, max_rounds: int, started: threading.Event | None = None
) -> None:
    store = _store()
    item = store.get(project_id)
    repo = item.get("repository_path")
    settings = _chatgpt_settings()
    provider_label = "third_party:" + str(model_configuration()["provider_host"])
    _auto_iteration.start(
        project_id,
        store=store,
        route_model=ThirdPartyChatModel.from_environment(None),
        claim_model=ThirdPartyChatModel.from_environment(None),
        claim_provider=provider_label,
        driver=PlaywrightChatGPTDriver(settings),
        project_name=str(item.get("name", "project")),
        repo_path=Path(str(repo)).expanduser() if isinstance(repo, str) and repo else None,
        max_rounds=max_rounds,
        started=started,
    )


@app.get("/api/projects/{project_id}/research-agent/loop")
def auto_iteration_status(project_id: str) -> dict[str, object]:
    return _auto_iteration.status(project_id)


@app.post("/api/projects/{project_id}/research-agent/loop")
def start_auto_iteration(project_id: str, body: LoopInput) -> dict[str, object]:
    try:
        _store().get(project_id)  # fail closed on unknown project before spawning
        if _auto_iteration.status(project_id)["running"]:
            raise ValueError("Auto iteration loop is already running for this project")
        started = threading.Event()
        threading.Thread(
            target=_run_auto_iteration_sync,
            args=(project_id, body.max_rounds, started),
            daemon=True,
        ).start()
        started.wait(timeout=2.0)
        return _auto_iteration.status(project_id)
    except (KeyError, ValueError, OSError) as error:
        _raise(error)
    return {}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787)


if __name__ == "__main__":
    main()
