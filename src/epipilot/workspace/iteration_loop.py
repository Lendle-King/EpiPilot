"""Bounded autonomous iteration loop: route -> ChatGPT Web -> review -> claims.

Runs one project's experiment route cycle repeatedly until a phase goal is
signalled (verdict ``goal_met``), a blocking verdict appears (``blocked``), the
round budget is exhausted, or a major problem fails closed. Verified work from
finished rounds is preserved: each round commits its report into the research
repository, re-pins the snapshot, and refreshes grounded claims before the
next route is generated.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from epipilot.workspace.algorithm_execution import (
    AlgorithmExecutorPort,
    ExecutionReceipt,
    ExecutionRequest,
    execution_summary,
    verify_execution,
)
from epipilot.workspace.chatgpt_web import extract_route_review_json
from epipilot.workspace.experiment_routes import (
    ChatGPTWebPort,
    ExperimentRoutePlanner,
    RouteModel,
    RouteReview,
    build_iteration_pack,
)
from epipilot.workspace.onboarding import WorkspaceStore, git
from epipilot.workspace.research_agent import ExtractionModel, ResearchOnboardingAgent

_STOP_VERDICTS = frozenset({"goal_met", "blocked"})
_HANDOFF_SECRET = re.compile(r"(?:TOKEN|PASSWORD|API[_ ]?KEY|SECRET|PRIVATE[_ ]?KEY)\s*[:=]", re.I)


def _host_repository_handoff(repo_path: Path) -> str:
    """Compile bounded, committed data-audit excerpts for the sandbox executor."""
    try:
        commit = git(repo_path, "rev-parse", "HEAD")
        paths = git(repo_path, "ls-tree", "-r", "--name-only", commit).splitlines()
    except (OSError, ValueError):
        return ""
    candidates = sorted(
        path
        for path in paths
        if path.startswith("docs/data_sources/")
        and path.lower().endswith((".md", ".txt"))
        and "validation" in path.lower()
    )
    if not candidates:
        return ""

    def last_commit_timestamp(path: str) -> int:
        try:
            return int(git(repo_path, "log", "-1", "--format=%ct", commit, "--", path))
        except (OSError, ValueError):
            return 0

    candidates.sort(key=lambda path: (last_commit_timestamp(path), path), reverse=True)
    sections: list[str] = [f"host_git_commit: {commit}"]
    remaining = 9_000
    for path in candidates[:4]:
        if remaining <= 0:
            break
        try:
            text = git(repo_path, "show", f"{commit}:{path}")
        except (OSError, ValueError):
            continue
        safe_lines = [line for line in text.splitlines() if not _HANDOFF_SECRET.search(line)]
        excerpt = "\n".join(safe_lines)[: min(3_000, remaining)]
        if not excerpt:
            continue
        section = f"--- {path} (exact committed excerpt) ---\n{excerpt}"
        sections.append(section)
        remaining -= len(section)
    return "\n".join(sections)


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
    algorithm_id: str | None = None
    route_run_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "round": self.round_index,
            "step": self.step,
            "message": self.message,
            "stop_reason": self.stop_reason,
            "verdicts": list(self.verdicts),
            "rounds_completed": self.rounds_completed,
            "algorithm_id": self.algorithm_id,
            "route_run_id": self.route_run_id,
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
        resume_route_run_id: str | None = None,
        algorithm_executor: AlgorithmExecutorPort | None = None,
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
                resume_route_run_id=resume_route_run_id,
                algorithm_executor=algorithm_executor,
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
        resume_route_run_id: str | None,
        algorithm_executor: AlgorithmExecutorPort | None,
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
        if algorithm_executor is not None and resume_route_run_id is None:
            pending = planner.report(project_id)
            pending_route = pending.get("route") or {}
            pending_review = pending.get("review") or {}
            if (
                pending_route.get("run_id")
                and pending_review.get("execution_run_id") == pending_route.get("run_id")
                and (checkpoint or {}).get("route_run_id") != pending_route.get("run_id")
            ):
                resume_route_run_id = str(pending_route["run_id"])
        try:
            for round_index in range(completed + 1, completed + max_rounds + 1):
                state.round_index = round_index
                state.step = "route"
                state.message = f"round {round_index}: generating experiment route"
                recovery_review: dict[str, Any] | None = None
                if resume_route_run_id is not None and round_index == completed + 1:
                    if (checkpoint or {}).get("route_run_id") == resume_route_run_id:
                        raise ValueError("Requested algorithm route already completed")
                    report = planner.report(project_id)
                    route = report.get("route") or {}
                    if route.get("run_id") != resume_route_run_id:
                        raise ValueError("Pending route does not match the requested resume route")
                    review = report.get("review") or {}
                    if review.get("created_at", "") >= route.get("created_at", ""):
                        if (
                            algorithm_executor is not None
                            and review.get("execution_run_id") == resume_route_run_id
                        ):
                            recovery_review = report
                        else:
                            raise ValueError(
                                "Pending route already has a review; refusing duplicate recovery"
                            )
                    planner.record_loop_resume(project_id, resume_route_run_id, round_index)
                else:
                    report = planner.generate(
                        project_id,
                        route_model,
                        claim_provider,
                        require_algorithm=algorithm_executor is not None,
                    )

                route = dict(report["route"])
                state.route_run_id = str(route["run_id"])
                execution: ExecutionReceipt | None = None
                manifest: Path | None = None
                if algorithm_executor is not None:
                    if repo_path is None:
                        raise ValueError("Algorithm experiments require a local repository")
                    request = ExecutionRequest(
                        route_run_id=str(route["run_id"]),
                        round_index=round_index,
                        algorithm_id=route["proposals"][0]["algorithm_id"],
                    )
                    state.step = "execute_algorithm"
                    state.message = f"round {round_index}: running {request.algorithm_id} locally"
                    manifest = algorithm_executor.execute(request)
                    execution = verify_execution(
                        repo_path, manifest, request.route_run_id, round_index
                    )
                    if execution.algorithm_id != request.algorithm_id:
                        raise ValueError("Execution algorithm does not match the selected route")
                    state.algorithm_id = execution.algorithm_id

                state.step = "chatgpt"
                state.message = f"round {round_index}: driving ChatGPT Web"
                pack = build_iteration_pack(
                    route,
                    project_name,
                    host_handoff=_host_repository_handoff(repo_path) if repo_path else "",
                )
                if execution is not None:
                    pack += (
                        "\n\nCURRENT ROUTE HOST EXECUTION (daily metrics independently checked):\n"
                        + execution_summary(execution)
                        + "\nThe host has NOW executed the algorithm and controls for this "
                        "route. Analyse THESE new results, not earlier diagnostic summaries. "
                        "The browser sandbox need not rerun inaccessible host data. Return the "
                        "normal review JSON plus execution_run_id equal to ROUTE_RUN_ID, "
                        "candidate_conclusion=accept|reject|inconclusive, and new_claims_zh "
                        "(1-5 nonblank grounded observations/hypotheses). Explain mechanism, "
                        "baseline deltas, costs, drawbacks and next algorithm choice. Include "
                        "algorithm analysis in report_markdown. Historical diagnostics cannot "
                        "justify goal_met. Claims are observations pending provenance validation."
                    )
                reviewed = recovery_review or self._review_with_repairs(
                    project_id, planner, driver, pack, execution
                )

                review = dict(reviewed.get("review") or {})
                if execution is not None:
                    self._validate_algorithm_review(review, execution)
                verdict = str(review.get("verdict", "")).strip().lower()
                if execution is not None and verdict == "goal_met":
                    # Public historical artifacts cannot satisfy an independent forward goal.
                    planner.record_loop_attempt(
                        project_id,
                        {
                            "route_run_id": execution.route_run_id,
                            "reason": "Historical goal_met normalized to continue",
                        },
                    )
                    verdict = "continue"
                state.step = "update_claims"
                state.message = f"round {round_index}: recording report and refreshing claims"
                report_markdown = str(review.get("report_markdown", ""))
                if repo_path is None or not report_markdown.strip():
                    raise ValueError("Round requires a repository and a nonblank experiment report")
                if execution is not None and manifest is not None:
                    archive = (
                        repo_path
                        / "docs/auto_iteration/experiments"
                        / (f"round_{round_index}_{execution.route_run_id}.json")
                    )
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    serialized = execution.model_dump_json(indent=2)
                    if archive.exists() and archive.read_text(encoding="utf-8") != serialized:
                        raise ValueError("Refusing to overwrite an archived execution receipt")
                    archive.write_text(serialized, encoding="utf-8")
                    git(repo_path, "add", str(archive.relative_to(repo_path)))
                    report_markdown += "\n\n" + execution_summary(execution)
                    report_markdown += "\n执行清单: " + str(archive.relative_to(repo_path))
                    report_markdown += "\n算法结论: " + str(review["candidate_conclusion"])
                    report_markdown += "\n\n待验证的新 claims(模型分析):\n" + "\n".join(
                        "- " + str(claim) for claim in review["new_claims_zh"]
                    )
                archived = planner.load_loop_archive(project_id, str(route["run_id"]), round_index)
                if archived is not None:
                    report_path = str(archived["report_path"])
                    blob_id = git(repo_path, "rev-parse", "HEAD:" + report_path)
                    current_bytes = (repo_path / report_path).read_bytes()
                    if (
                        blob_id != archived["blob_id"]
                        or hashlib.sha256(current_bytes).hexdigest() != archived["sha256"]
                    ):
                        raise ValueError("Previously archived report changed; refusing recovery")
                else:
                    report_path = self._commit_round_report(repo_path, round_index, report_markdown)
                    planner.record_loop_archive(
                        project_id,
                        {
                            "route_run_id": route["run_id"],
                            "round": round_index,
                            "report_path": report_path,
                            "sha256": hashlib.sha256(
                                (repo_path / report_path).read_bytes()
                            ).hexdigest(),
                            "blob_id": git(repo_path, "rev-parse", "HEAD:" + report_path),
                        },
                    )
                store.repin_snapshot(project_id)
                agent.run_model(
                    project_id,
                    claim_model,
                    claim_provider,
                    claim_max_documents,
                    priority_paths=(report_path,),
                )
                refreshed = agent.report(project_id)
                if any(
                    entry.get("path") == report_path
                    for entry in refreshed.get(
                        "unresolved_rejected_documents",
                        refreshed.get("rejected_documents", []),
                    )
                ) or not any(
                    ref.get("source_path") == report_path
                    for claim in refreshed.get("claims", [])
                    for ref in claim.get("citations", [])
                ):
                    raise ValueError("Archived experiment report did not produce grounded claims")
                state.verdicts.append(verdict)
                state.rounds_completed += 1
                if verdict in _STOP_VERDICTS or verdict.startswith("goal_met"):
                    state.step = "done"
                    state.message = f"round {round_index}: verdict {verdict}"
                    state.stop_reason = f"阶段判定停止: {verdict}"
                    planner.record_loop_checkpoint(project_id, state.snapshot())
                    return
                planner.record_loop_checkpoint(project_id, state.snapshot())
            state.step = "done"
            state.stop_reason = f"达到最大轮次 {max_rounds}"
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
            state.step = "stopped"
            state.message = str(error)
            state.stop_reason = f"重大问题: {error}"

    @staticmethod
    def _validate_algorithm_review(review: dict[str, Any], execution: ExecutionReceipt) -> None:
        checked = RouteReview.model_validate(review)
        if (
            checked.execution_run_id != execution.route_run_id
            or checked.candidate_conclusion is None
            or not checked.new_claims_zh
            or not all(claim.strip() for claim in checked.new_claims_zh)
            or not checked.report_markdown.strip()
            or checked.verdict not in {"continue", "goal_met"}
        ):
            raise ValueError(
                "Review requires current execution ID, algorithm conclusion, "
                "nonblank new claims and report; blocked is not a completed round"
            )

    @staticmethod
    def _review_with_repairs(
        project_id: str,
        planner: ExperimentRoutePlanner,
        driver: ChatGPTWebPort,
        pack: str,
        execution: ExecutionReceipt | None,
    ) -> dict[str, Any]:
        """Bounded, changed-information repairs stay inside the original round."""
        attempts = 3 if execution is not None else 1
        correction = ""
        for attempt in range(1, attempts + 1):
            reply = driver.submit(pack + correction)
            try:
                raw = extract_route_review_json(reply)
                if execution is not None:
                    review = RouteReview.model_validate_json(raw)
                    AutoIterationLoop._validate_algorithm_review(review.model_dump(), execution)
                return planner.import_review(project_id, raw)
            except ValueError as error:
                if execution is None:
                    raise
                reason = str(error)[:600]
                planner.record_loop_attempt(
                    project_id,
                    {
                        "route_run_id": execution.route_run_id,
                        "attempt": attempt,
                        "reason": reason,
                    },
                )
                correction = (
                    f"\nIN-ROUND REPAIR {attempt}: previous review rejected: {reason}. "
                    "Use the verified current host results; do not rerun the same experiment. "
                    "Correct the JSON and explain the candidate result with new claims."
                )
        raise ValueError(
            "Algorithm review repair budget exhausted; current round remains incomplete"
        )

    @staticmethod
    def _commit_round_report(repo_path: Path, round_index: int, markdown: str) -> str:
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
        return str(path.relative_to(repo_path))

    @staticmethod
    def _persist_summary(project_id: str, store: WorkspaceStore, state: LoopState) -> None:
        ExperimentRoutePlanner(store).record_loop_summary(project_id, state.snapshot())
