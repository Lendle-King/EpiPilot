"""Executor port for coding-agent backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from epipilot.core.models import Task


@dataclass(frozen=True, slots=True)
class ExecutorObservation:
    """Non-authoritative report returned by a coding-agent executor."""

    summary: str
    changed_files: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


class CodingAgentExecutor(Protocol):
    """Interface implemented by Pi, Codex, Claude Code, and other adapters."""

    async def start_task(self, task: Task, context: str) -> str:
        """Start an isolated executor session and return its session identifier."""
        ...

    async def inspect(self, session_id: str) -> ExecutorObservation:
        """Return the latest non-authoritative executor observation."""
        ...

    async def interrupt(self, session_id: str, reason: str) -> None:
        """Interrupt a running executor session."""
        ...

    async def terminate(self, session_id: str) -> None:
        """Terminate and clean up an executor session."""
        ...
