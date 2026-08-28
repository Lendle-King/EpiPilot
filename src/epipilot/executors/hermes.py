"""Bounded Hermes coding-agent executor adapter.

Hermes is treated as a non-authoritative editing executor. It may report that a
task is complete, but only EpiPilot's verifier can transition that task to PASSED.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from epipilot.core.models import Task
from epipilot.executors.base import ExecutorObservation, ExecutorState
from epipilot.integrations.hermes.runtime_contract import (
    EXECUTOR_CHILD_ENV,
    EXECUTOR_TOOLSET,
    EXECUTOR_WORKSPACE_ENV,
)


@dataclass(frozen=True, slots=True)
class _CapturedOutput:
    data: bytes
    truncated: bool


@dataclass(slots=True)
class _HermesSession:
    process: asyncio.subprocess.Process
    wait_task: asyncio.Task[int]
    stdout_task: asyncio.Task[_CapturedOutput]
    stderr_task: asyncio.Task[_CapturedOutput]
    scratch_dir: Path
    prompt_path: Path
    interrupted_reason: str | None = None
    final_observation: ExecutorObservation | None = None


async def _drain_stream(
    stream: asyncio.StreamReader,
    *,
    limit_bytes: int,
) -> _CapturedOutput:
    """Drain a child stream completely while retaining only a bounded prefix."""
    kept = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        remaining = max(0, limit_bytes - len(kept))
        if remaining:
            kept.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return _CapturedOutput(bytes(kept), truncated)


def _write_private_text(path: Path, content: str) -> None:
    """Create one owner-only prompt file without ever placing content in argv."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | int(getattr(os, "O_BINARY", 0))
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except Exception:
        with suppress(OSError):
            path.unlink(missing_ok=True)
        raise


@dataclass(slots=True)
class HermesExecutor:
    """Run one bounded Hermes single-query child per EpiPilot task attempt.

    The child receives the task through Hermes' ``chat --query-file`` transport so
    project context is not exposed through the process argument list. ``--ignore-rules``
    prevents ambient Hermes memory/SOUL/AGENTS state from becoming executor context.
    Only the native ``file`` toolset plus EpiPilot's guard sentinel are enabled; the
    EpiPilot Hermes plugin then enforces workspace containment on every file call.

    This adapter intentionally does not expose terminal/process execution yet. Command
    execution remains on EpiPilot's verifier side until TaskContract command/resource
    policy is enforced at this boundary.
    """

    workspace: Path
    command: tuple[str, ...] = ("hermes",)
    max_turns: int = 80
    run_budget_seconds: float | None = None
    shutdown_timeout_seconds: float = 5.0
    output_limit_bytes: int = 64 * 1024
    summary_max_chars: int = 2000
    require_clean_worktree: bool = True
    _sessions: dict[str, _HermesSession] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.workspace = self.workspace.expanduser().resolve()
        if not self.command or any(not item for item in self.command):
            raise ValueError("Hermes command must contain non-empty argv entries")
        if self.max_turns < 1:
            raise ValueError("max_turns must be positive")
        if self.run_budget_seconds is not None and self.run_budget_seconds <= 0:
            raise ValueError("run_budget_seconds must be positive when provided")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown timeout must be positive")
        if self.output_limit_bytes < 1024:
            raise ValueError("output limit must be at least 1024 bytes")
        if self.summary_max_chars < 128:
            raise ValueError("summary character limit must be at least 128")

    async def start_task(self, task: Task, context: str) -> str:
        """Start one isolated Hermes single-query child and return an opaque id."""
        if not context.strip():
            raise ValueError("executor context must not be empty")
        await self._validate_workspace()

        scratch_dir = Path(tempfile.mkdtemp(prefix="epipilot-hermes-"))
        prompt_path = scratch_dir / "prompt.txt"
        try:
            _write_private_text(prompt_path, self._build_prompt(task, context))
            argv = self._build_argv(prompt_path)
            env = os.environ.copy()
            env[EXECUTOR_CHILD_ENV] = "1"
            env[EXECUTOR_WORKSPACE_ENV] = str(self.workspace)
            # Hermes file tools resolve relative paths against TERMINAL_CWD.
            env["TERMINAL_CWD"] = str(self.workspace)

            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.workspace,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception:
            shutil.rmtree(scratch_dir, ignore_errors=True)
            raise

        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            shutil.rmtree(scratch_dir, ignore_errors=True)
            raise RuntimeError("Hermes child did not expose captured stdout/stderr")

        session_id = f"hermes-{uuid4()}"
        self._sessions[session_id] = _HermesSession(
            process=process,
            wait_task=asyncio.create_task(process.wait()),
            stdout_task=asyncio.create_task(
                _drain_stream(process.stdout, limit_bytes=self.output_limit_bytes)
            ),
            stderr_task=asyncio.create_task(
                _drain_stream(process.stderr, limit_bytes=self.output_limit_bytes)
            ),
            scratch_dir=scratch_dir,
            prompt_path=prompt_path,
        )
        return session_id

    async def inspect(self, session_id: str) -> ExecutorObservation:
        """Map child lifecycle to non-authoritative EpiPilot observations."""
        session = self._require_session(session_id)
        if session.final_observation is not None:
            return session.final_observation
        if not session.wait_task.done():
            return ExecutorObservation(
                state=ExecutorState.RUNNING,
                summary="Hermes executor child is still running.",
            )

        returncode = await session.wait_task
        stdout, _stderr = await asyncio.gather(session.stdout_task, session.stderr_task)

        if session.interrupted_reason is not None:
            observation = ExecutorObservation(
                state=ExecutorState.BLOCKED,
                summary=f"Hermes executor was interrupted: {session.interrupted_reason}",
                failure_signature="hermes-interrupted",
            )
            session.final_observation = observation
            return observation

        if returncode != 0:
            observation = ExecutorObservation(
                state=ExecutorState.FAILED,
                summary=(
                    "Hermes executor exited unsuccessfully; raw child stderr is intentionally "
                    "not promoted into canonical state."
                ),
                failure_signature=f"hermes-exit-{returncode}",
            )
            session.final_observation = observation
            return observation

        report = stdout.data.decode("utf-8", errors="replace").strip()
        if not report:
            observation = ExecutorObservation(
                state=ExecutorState.FAILED,
                summary="Hermes executor exited without a final report.",
                failure_signature="hermes-empty-report",
            )
            session.final_observation = observation
            return observation

        try:
            changed_files = await self._changed_files()
        except RuntimeError:
            observation = ExecutorObservation(
                state=ExecutorState.FAILED,
                summary="Hermes finished, but EpiPilot could not inspect workspace changes safely.",
                failure_signature="hermes-workspace-inspection-failed",
            )
            session.final_observation = observation
            return observation

        report = report[: self.summary_max_chars]
        if stdout.truncated or len(stdout.data.decode("utf-8", errors="replace")) > len(report):
            report += "\n[executor report truncated]"
        observation = ExecutorObservation(
            state=ExecutorState.REPORTED_DONE,
            summary=(
                "Hermes reported completion. This report is non-authoritative and requires "
                f"independent EpiPilot verification.\n{report}"
            ),
            changed_files=changed_files,
        )
        session.final_observation = observation
        return observation

    async def interrupt(self, session_id: str, reason: str) -> None:
        """Request bounded child termination; the resulting observation is BLOCKED."""
        reason = reason.strip()
        if not reason:
            raise ValueError("interrupt reason must not be empty")
        session = self._require_session(session_id)
        if session.interrupted_reason is None:
            session.interrupted_reason = reason
        if not session.wait_task.done():
            session.process.terminate()

    async def terminate(self, session_id: str) -> None:
        """Terminate the child if needed and remove transient prompt material."""
        session = self._require_session(session_id)
        try:
            if not session.wait_task.done():
                session.process.terminate()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(session.wait_task),
                        timeout=self.shutdown_timeout_seconds,
                    )
                except TimeoutError:
                    session.process.kill()
                    await session.wait_task
            await asyncio.gather(
                session.stdout_task,
                session.stderr_task,
                return_exceptions=True,
            )
        finally:
            shutil.rmtree(session.scratch_dir, ignore_errors=True)
            self._sessions.pop(session_id, None)

    def _build_argv(self, prompt_path: Path) -> tuple[str, ...]:
        argv = [
            *self.command,
            "chat",
            "--query-file",
            str(prompt_path),
            "-Q",
            "--in",
            str(self.workspace),
            "--ignore-rules",
            "-t",
            f"{EXECUTOR_TOOLSET},file",
            "--source",
            "tool",
            "--max-turns",
            str(self.max_turns),
        ]
        if self.run_budget_seconds is not None:
            argv.extend(("--run-budget", str(self.run_budget_seconds)))
        return tuple(argv)

    @staticmethod
    def _build_prompt(task: Task, context: str) -> str:
        return (
            "You are a bounded coding executor controlled by EpiPilot. Work only on the "
            "current task in the current workspace. Use file tools only; shell/terminal, "
            "delegation, memory mutation, network actions, and user interaction are outside "
            "your authority. Your final response is an observation, never proof that the "
            "task passed; EpiPilot will run independent verification afterward.\n\n"
            f"Task objective:\n{task.objective}\n\n"
            f"EpiPilot compiled context:\n{context.strip()}\n"
        )

    async def _validate_workspace(self) -> None:
        if not os.path.isdir(self.workspace):
            raise ValueError(f"Hermes workspace does not exist: {self.workspace}")
        returncode, stdout, _stderr = await self._git("rev-parse", "--show-toplevel")
        if returncode != 0:
            raise ValueError("HermesExecutor requires a Git working tree")
        repository_root = stdout.decode("utf-8", errors="replace").strip()
        if os.path.normcase(repository_root) != os.path.normcase(str(self.workspace)):
            raise ValueError("HermesExecutor workspace must be the Git repository root")
        if self.require_clean_worktree:
            returncode, stdout, _stderr = await self._git(
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            )
            if returncode != 0:
                raise RuntimeError("could not inspect Git workspace cleanliness")
            if stdout:
                raise ValueError(
                    "HermesExecutor requires a clean workspace so executor changes "
                    "remain attributable"
                )

    async def _changed_files(self) -> tuple[str, ...]:
        returncode, tracked, _stderr = await self._git(
            "diff",
            "--name-only",
            "-z",
            "HEAD",
            "--",
        )
        if returncode != 0:
            raise RuntimeError("git diff failed")
        returncode, untracked, _stderr = await self._git(
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        if returncode != 0:
            raise RuntimeError("git ls-files failed")
        paths = {
            item.decode("utf-8", errors="surrogateescape")
            for blob in (tracked, untracked)
            for item in blob.split(b"\0")
            if item
        }
        return tuple(sorted(paths))

    async def _git(self, *args: str) -> tuple[int, bytes, bytes]:
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=self.workspace,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("git executable is required by HermesExecutor") from exc
        stdout, stderr = await process.communicate()
        if process.returncode is None:
            raise RuntimeError("git subprocess did not terminate")
        return process.returncode, stdout, stderr

    def _require_session(self, session_id: str) -> _HermesSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"unknown Hermes executor session: {session_id}") from exc
