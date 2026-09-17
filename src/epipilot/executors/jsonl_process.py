"""Shared lifecycle for one-shot JSONL coding-agent subprocesses."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from epipilot.core.models import Task
from epipilot.executors.base import ExecutorObservation, ExecutorState


@dataclass(slots=True)
class _JsonlSession:
    """Mutable process state owned by one executor adapter session."""

    process: asyncio.subprocess.Process
    state: ExecutorState = ExecutorState.RUNNING
    summary: str = "JSONL executor process started"
    reader_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class JsonlProcessExecutor:
    """Base implementation for one-task-per-process JSONL executors.

    Subclasses define only the default command plus backend-specific event and
    process-exit interpretation. Natural-language output remains non-authoritative.
    """

    command: tuple[str, ...] = ()
    cwd: Path | None = None
    shutdown_timeout_seconds: float = 5.0
    _sessions: dict[str, _JsonlSession] = field(default_factory=dict, init=False)

    backend_name: ClassVar[str] = "JSONL executor"

    def __post_init__(self) -> None:
        if not self.command or any(not part for part in self.command):
            raise ValueError(f"{self.backend_name} command must contain non-empty argv entries")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown timeout must be positive")

    async def start_task(self, task: Task, context: str) -> str:
        """Start one non-interactive executor process for a task."""
        if not context.strip():
            raise ValueError(f"{self.backend_name} executor context must not be empty")

        prompt = f"{context.rstrip()}\n\n[Current task]\n{task.objective}"
        process = await asyncio.create_subprocess_exec(
            *self.command,
            prompt,
            cwd=str(self.cwd) if self.cwd is not None else None,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise RuntimeError(f"{self.backend_name} subprocess did not expose required pipes")

        session_id = str(uuid4())
        session = _JsonlSession(process=process, summary=f"{self.backend_name} process started")
        self._sessions[session_id] = session
        session.reader_task = asyncio.create_task(self._read_stdout(session))
        session.stderr_task = asyncio.create_task(self._drain_stderr(session))
        return session_id

    async def inspect(self, session_id: str) -> ExecutorObservation:
        """Return the latest non-authoritative process observation."""
        session = self._session(session_id)
        await asyncio.sleep(0)
        return ExecutorObservation(state=session.state, summary=session.summary)

    async def interrupt(self, session_id: str, reason: str) -> None:
        """Request subprocess termination without converting it into project truth."""
        session = self._session(session_id)
        if session.process.returncode is not None:
            return
        session.summary = f"{self.backend_name} interrupt requested: {reason}"
        session.process.terminate()

    async def terminate(self, session_id: str) -> None:
        """Terminate the subprocess and release all session-owned tasks."""
        session = self._session(session_id)
        process = session.process
        try:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=self.shutdown_timeout_seconds)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        finally:
            for task in (session.reader_task, session.stderr_task):
                if task is not None and not task.done():
                    task.cancel()
                if task is not None:
                    with suppress(asyncio.CancelledError):
                        await task
            self._sessions.pop(session_id, None)

    async def _read_stdout(self, session: _JsonlSession) -> None:
        stdout = session.process.stdout
        if stdout is None:
            session.state = ExecutorState.FAILED
            session.summary = f"{self.backend_name} stdout pipe disappeared"
            return

        while True:
            line = await stdout.readline()
            if not line:
                break
            if session.state is not ExecutorState.RUNNING:
                continue
            record = line.removesuffix(b"\n").removesuffix(b"\r")
            if not record:
                continue
            try:
                payload = json.loads(record)
            except json.JSONDecodeError:
                session.state = ExecutorState.FAILED
                session.summary = f"{self.backend_name} emitted malformed JSONL"
                continue
            if not isinstance(payload, dict):
                session.state = ExecutorState.FAILED
                session.summary = f"{self.backend_name} emitted a non-object JSON record"
                continue
            self._handle_record(session, payload)

        return_code = await session.process.wait()
        if session.state is ExecutorState.RUNNING:
            self._handle_process_exit(session, return_code)

    async def _drain_stderr(self, session: _JsonlSession) -> None:
        stderr = session.process.stderr
        if stderr is None:
            return
        while await stderr.readline():
            # Drain only. Raw model/runtime stderr is not canonical project evidence.
            pass

    def _handle_record(self, session: _JsonlSession, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def _handle_process_exit(self, session: _JsonlSession, return_code: int) -> None:
        session.state = ExecutorState.FAILED
        session.summary = (
            f"{self.backend_name} process exited with code {return_code} before completion"
        )

    def _session(self, session_id: str) -> _JsonlSession:
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise KeyError(f"unknown {self.backend_name} session: {session_id}") from error
