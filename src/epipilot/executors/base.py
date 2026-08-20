"""Executor port for coding-agent backends."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from epipilot.core.models import Task


class ExecutorState(StrEnum):
    """Lifecycle state reported by a non-authoritative executor adapter."""

    RUNNING = "running"
    REPORTED_DONE = "reported_done"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExecutorObservation:
    """Non-authoritative report returned by a coding-agent executor."""

    state: ExecutorState
    summary: str
    changed_files: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    failure_signature: str | None = None

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("executor observation summary must not be empty")
        if self.failure_signature is not None and not self.failure_signature.strip():
            raise ValueError("failure signature must be non-empty when provided")


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
