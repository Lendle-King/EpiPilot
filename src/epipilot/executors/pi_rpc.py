"""Headless Pi executor using Pi's JSONL RPC mode over stdin/stdout."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from epipilot.core.models import Task
from epipilot.executors.base import CodingAgentExecutor, ExecutorObservation, ExecutorState

_INTERACTIVE_UI_METHODS = frozenset({"select", "confirm", "input", "editor"})


@dataclass(slots=True)
class _PiSession:
    process: asyncio.subprocess.Process
    state: ExecutorState = ExecutorState.RUNNING
    summary: str = "Pi RPC session started"
    reader_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class PiRpcExecutor(CodingAgentExecutor):
    """One-process-per-task Pi RPC adapter for the V0 runtime.

    The adapter intentionally does not interpret Pi's streamed natural-language output as
    canonical evidence. Interactive extension requests are surfaced as ``BLOCKED`` rather
    than answered automatically.
    """

    command: tuple[str, ...] = ("pi", "--mode", "rpc", "--no-session")
    cwd: Path | None = None
    shutdown_timeout_seconds: float = 5.0
    _sessions: dict[str, _PiSession] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if not self.command or any(not part for part in self.command):
            raise ValueError("Pi RPC command must contain non-empty argv entries")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown timeout must be positive")

    async def start_task(self, task: Task, context: str) -> str:
        if not context.strip():
            raise ValueError("Pi executor context must not be empty")

        process = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=str(self.cwd) if self.cwd is not None else None,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise RuntimeError("Pi RPC subprocess did not expose required stdio pipes")

        session_id = str(uuid4())
        session = _PiSession(process=process)
        self._sessions[session_id] = session
        session.reader_task = asyncio.create_task(self._read_stdout(session))
        session.stderr_task = asyncio.create_task(self._drain_stderr(session))

        message = f"{context.rstrip()}\n\n[Current task]\n{task.objective}"
        await self._send(
            session,
            {
                "id": f"prompt-{uuid4()}",
                "type": "prompt",
                "message": message,
            },
        )
        return session_id

    async def inspect(self, session_id: str) -> ExecutorObservation:
        session = self._session(session_id)
        await asyncio.sleep(0)

        if session.process.returncode is not None and session.state is ExecutorState.RUNNING:
            session.state = ExecutorState.FAILED
            session.summary = f"Pi RPC process exited with code {session.process.returncode}"

        return ExecutorObservation(
            state=session.state,
            summary=session.summary,
        )

    async def interrupt(self, session_id: str, reason: str) -> None:
        session = self._session(session_id)
        if session.process.returncode is not None:
            return
        await self._send(
            session,
            {
                "id": f"abort-{uuid4()}",
                "type": "abort",
            },
        )
        session.summary = f"Pi RPC abort requested: {reason}"

    async def terminate(self, session_id: str) -> None:
        session = self._session(session_id)
        process = session.process
        try:
            if process.returncode is None:
                with suppress(BrokenPipeError, ConnectionResetError):
                    await self._send(
                        session,
                        {
                            "id": f"abort-{uuid4()}",
                            "type": "abort",
                        },
                    )
                if process.stdin is not None:
                    process.stdin.close()
                try:
                    await asyncio.wait_for(
                        process.wait(),
                        timeout=self.shutdown_timeout_seconds,
                    )
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

    async def _read_stdout(self, session: _PiSession) -> None:
        stdout = session.process.stdout
        if stdout is None:
            session.state = ExecutorState.FAILED
            session.summary = "Pi RPC stdout pipe disappeared"
            return

        while True:
            line = await stdout.readline()
            if not line:
                break
            record = line.removesuffix(b"\n").removesuffix(b"\r")
            if not record:
                continue
            try:
                payload = json.loads(record)
            except json.JSONDecodeError:
                session.state = ExecutorState.FAILED
                session.summary = "Pi RPC emitted malformed JSONL"
                return
            if not isinstance(payload, dict):
                session.state = ExecutorState.FAILED
                session.summary = "Pi RPC emitted a non-object JSON record"
                return

            self._handle_record(session, payload)
            if session.state in {
                ExecutorState.REPORTED_DONE,
                ExecutorState.BLOCKED,
                ExecutorState.FAILED,
            }:
                return

        if session.state is ExecutorState.RUNNING:
            session.state = ExecutorState.FAILED
            session.summary = "Pi RPC stdout closed before agent_end"

    async def _drain_stderr(self, session: _PiSession) -> None:
        stderr = session.process.stderr
        if stderr is None:
            return
        while await stderr.readline():
            # Drain to avoid subprocess backpressure. Raw stderr is intentionally not
            # promoted into canonical state or retained as memory by this adapter.
            pass

    def _handle_record(self, session: _PiSession, payload: dict[str, Any]) -> None:
        record_type = payload.get("type")
        if not isinstance(record_type, str):
            session.state = ExecutorState.FAILED
            session.summary = "Pi RPC record is missing a string type"
            return

        if record_type == "response" and payload.get("success") is False:
            session.state = ExecutorState.FAILED
            session.summary = "Pi RPC command returned an unsuccessful response"
            return

        if record_type == "extension_ui_request":
            method = payload.get("method")
            if isinstance(method, str) and method in _INTERACTIVE_UI_METHODS:
                session.state = ExecutorState.BLOCKED
                session.summary = f"Pi requires user-owned interactive input: {method}"
                return

        if record_type == "agent_end":
            session.state = ExecutorState.REPORTED_DONE
            session.summary = "Pi reported agent_end; independent verification is required"
            return

        session.summary = f"Pi RPC event: {record_type}"

    async def _send(self, session: _PiSession, payload: dict[str, object]) -> None:
        stdin = session.process.stdin
        if stdin is None or session.process.returncode is not None:
            raise BrokenPipeError("Pi RPC stdin is unavailable")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        stdin.write(encoded + b"\n")
        await stdin.drain()

    def _session(self, session_id: str) -> _PiSession:
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise KeyError(f"unknown Pi RPC session: {session_id}") from error
