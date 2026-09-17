"""Headless DeepSeek Harness executor using machine-readable JSONL output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from epipilot.executors.base import ExecutorState
from epipilot.executors.jsonl_process import JsonlProcessExecutor, _JsonlSession


@dataclass(slots=True)
class DshHeadlessExecutor(JsonlProcessExecutor):
    """One-shot DSH adapter that requires a final event and successful exit."""

    command: tuple[str, ...] = ("dsh", "--profile", "headless", "--json")

    backend_name: ClassVar[str] = "DSH headless"

    def _handle_record(self, session: _JsonlSession, payload: dict[str, Any]) -> None:
        record_type = payload.get("type")
        if not isinstance(record_type, str):
            session.state = ExecutorState.FAILED
            session.summary = "DSH headless JSONL record is missing a string type"
            return

        if record_type == "error":
            session.state = ExecutorState.FAILED
            session.summary = "DSH headless reported an error event"
            return

        if record_type == "final":
            session.metadata["saw_final"] = True
            session.summary = "DSH headless emitted final; waiting for process exit"
            return

        session.summary = f"DSH headless JSONL event: {record_type}"

    def _handle_process_exit(self, session: _JsonlSession, return_code: int) -> None:
        saw_final = session.metadata.get("saw_final") is True
        if return_code == 0 and saw_final:
            session.state = ExecutorState.REPORTED_DONE
            session.summary = "DSH headless completed; independent verification is required"
            return

        session.state = ExecutorState.FAILED
        if return_code == 0:
            session.summary = "DSH headless exited successfully without final event"
        else:
            session.summary = f"DSH headless process exited with code {return_code}"
