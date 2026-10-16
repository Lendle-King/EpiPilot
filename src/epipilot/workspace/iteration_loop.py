"""Bounded autonomous iteration loop: route -> ChatGPT Web -> review -> claims.

Runs one project's experiment route cycle repeatedly until a phase goal is
signalled (verdict ``goal_met``), a blocking verdict appears (``blocked``), the
round budget is exhausted, or a major problem fails closed. Verified work from
finished rounds is preserved: each round commits its report into the research
repository, re-pins the snapshot, and refreshes grounded claims before the
next route is generated.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from epipilot.workspace.chatgpt_web import extract_route_review_json
from epipilot.workspace.experiment_routes import (
    ChatGPTWebPort,
    ExperimentRoutePlanner,
    RouteModel,
    build_iteration_pack,
)
from epipilot.workspace.onboarding import WorkspaceStore, git
from epipilot.workspace.research_agent import ExtractionModel, ResearchOnboardingAgent

_STOP_VERDICTS = frozenset({"goal_met", "blocked"})


@dataclass(slots=True)
class LoopState:
    """Observable progress of one project's autonomous iteration."""

    running: bool = False
    round_index: int = 0
    step: str = "idle"
    message: str = ""
    stop_reason: str | None = None
    verdicts: list[str] = field(default_factory=list)
    rounds_completed: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "round": self.round_index,
            "step": self.step,
            "message": self.message,
            "stop_reason": self.stop_reason,
            "verdicts": list(self.verdicts),
            "rounds_completed": self.rounds_completed,
        }


class AutoIterationLoop:
    """Per-project bounded loop with single-flight execution and audit records."""

    def __init__(self) -> None:
        self._states: dict[str, LoopState] = {}
        self._lock = threading.Lock()

    def status(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._states.get(project_id)
            return state.snapshot() if state else LoopState().snapshot()

    def start(
        self,
        project_id: str,
        *,
        store: WorkspaceStore,
        route_model: RouteModel,
        claim_model: ExtractionModel,
        claim_provider: str,
        driver: ChatGPTWebPort,
        project_name: str,
        repo_path: Path | None = None,
        max_rounds: int = 3,
        claim_max_documents: int = 12,
        started: threading.Event | None = None,
    ) -> dict[str, Any]:
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        with self._lock:
            state = self._states.setdefault(project_id, LoopState())
            if state.running:
                raise ValueError("Auto iteration loop is already running for this project")
            state.running = True
            state.stop_reason = None
            state.verdicts = []
            state.rounds_completed = 0
            state.step = "route"
            state.message = "starting"
        if started is not None:
            started.set()
        try:
            self._run(
                project_id,
                state,
                store=store,
                route_model=route_model,
                claim_model=claim_model,
                claim_provider=claim_provider,
                driver=driver,
                project_name=project_name,
                repo_path=repo_path,
                max_rounds=max_rounds,
                claim_max_documents=claim_max_documents,
            )
        finally:
            state.running = False
            self._persist_summary(project_id, store, state)
        return self.status(project_id)

    def _run(
        self,
        project_id: str,
        state: LoopState,
        *,
        store: WorkspaceStore,
        route_model: RouteModel,
        claim_model: ExtractionModel,
        claim_provider: str,
        driver: ChatGPTWebPort,
        project_name: str,
        repo_path: Path | None,
        max_rounds: int,
        claim_max_documents: int,
    ) -> None:
        planner = ExperimentRoutePlanner(store)
        agent = ResearchOnboardingAgent(store)
        checkpoint = planner.load_loop_checkpoint(project_id)
        completed = int(checkpoint.get("rounds_completed", 0)) if checkpoint else 0
        if checkpoint and checkpoint.get("verdicts"):
            state.verdicts = [str(v) for v in checkpoint["verdicts"]]
        state.rounds_completed = completed
        if completed:
            state.message = f"resuming from checkpoint: {completed} rounds already completed"
        try:
            for round_index in range(completed + 1, completed + max_rounds + 1):
                state.round_index = round_index
                state.step = "route"
                state.message = f"round {round_index}: generating experiment route"
                report = planner.generate(project_id, route_model, claim_provider)

                state.step = "chatgpt"
                state.message = f"round {round_index}: driving ChatGPT Web"
                pack = build_iteration_pack(dict(report["route"]), project_name)
                reply = driver.submit(pack)
                reviewed = planner.import_review(project_id, extract_route_review_json(reply))

                review = dict(reviewed.get("review") or {})
                verdict = str(review.get("verdict", "")).strip().lower()
                state.verdicts.append(verdict)
                state.rounds_completed += 1
                if verdict in _STOP_VERDICTS or verdict.startswith("goal_met"):
                    state.step = "done"
                    state.message = f"round {round_index}: verdict {verdict}"
                    state.stop_reason = f"阶段判定停止: {verdict}"
                    planner.record_loop_checkpoint(project_id, state.snapshot())
                    return

                state.step = "update_claims"
                state.message = f"round {round_index}: recording report and refreshing claims"
                report_markdown = str(review.get("report_markdown", ""))
                if report_markdown and repo_path is not None:
                    self._commit_round_report(repo_path, round_index, report_markdown)
                    store.repin_snapshot(project_id)
                agent.run_model(project_id, claim_model, claim_provider, claim_max_documents)
                planner.record_loop_checkpoint(project_id, state.snapshot())
            state.step = "done"
            state.stop_reason = f"达到最大轮次 {max_rounds}"
        except (ValueError, RuntimeError, OSError) as error:
            state.step = "stopped"
            state.message = str(error)
            state.stop_reason = f"重大问题: {error}"

    @staticmethod
    def _commit_round_report(repo_path: Path, round_index: int, markdown: str) -> None:
        target_dir = repo_path / "docs" / "auto_iteration"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        path = target_dir / f"round_{round_index}_{stamp}.md"
        path.write_text(markdown, encoding="utf-8")
        git(repo_path, "add", str(path.relative_to(repo_path)))
        git(
            repo_path,
            "commit",
            "-m",
            f"docs(auto-iteration): round {round_index} experiment report",
        )

    @staticmethod
    def _persist_summary(project_id: str, store: WorkspaceStore, state: LoopState) -> None:
        ExperimentRoutePlanner(store).record_loop_summary(project_id, state.snapshot())
