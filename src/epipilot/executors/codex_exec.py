"""Non-interactive Codex executor using ``codex exec --json``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from epipilot.executors.base import ExecutorState
from epipilot.executors.jsonl_process import JsonlProcessExecutor, _JsonlSession


@dataclass(slots=True)
class CodexExecExecutor(JsonlProcessExecutor):
    """One-shot Codex CLI adapter whose completion remains non-authoritative."""

    command: tuple[str, ...] = ("codex", "exec", "--json", "--ephemeral")

    backend_name: ClassVar[str] = "Codex"

    def _handle_record(self, session: _JsonlSession, payload: dict[str, Any]) -> None:
        record_type = payload.get("type")
        if not isinstance(record_type, str):
            session.state = ExecutorState.FAILED
            session.summary = "Codex JSONL record is missing a string type"
            return

        if record_type == "turn.completed":
            session.state = ExecutorState.REPORTED_DONE
            session.summary = "Codex reported turn.completed; independent verification is required"
            return

        if record_type in {"turn.failed", "error"}:
            session.state = ExecutorState.FAILED
            session.summary = f"Codex reported terminal failure event: {record_type}"
            return

        session.summary = f"Codex JSONL event: {record_type}"

    def _handle_process_exit(self, session: _JsonlSession, return_code: int) -> None:
        session.state = ExecutorState.FAILED
        if return_code == 0:
            session.summary = "Codex process exited successfully without turn.completed"
        else:
            session.summary = f"Codex process exited with code {return_code} before turn.completed"
