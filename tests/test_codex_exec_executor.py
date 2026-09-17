from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest

from epipilot.core.models import Task, new_task_id
from epipilot.executors.base import ExecutorState
from epipilot.executors.codex_exec import CodexExecExecutor

_COMPLETED_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    prompt = sys.argv[-1]
    assert "project context" in prompt
    assert "Implement feature" in prompt
    print(json.dumps({"type": "thread.started", "thread_id": "thread-1"}), flush=True)
    print(json.dumps({"type": "turn.started"}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {"output_tokens": 1}}), flush=True)
    """
)

_FAILED_SCRIPT = textwrap.dedent(
    """
    import json

    print(json.dumps({"type": "turn.failed", "error": {"message": "boom"}}), flush=True)
    """
)

_MALFORMED_SCRIPT = "print('{not-json', flush=True)"


async def _wait_for_terminal(executor: CodexExecExecutor, session_id: str) -> ExecutorState:
    for _ in range(100):
        observation = await executor.inspect(session_id)
        if observation.state is not ExecutorState.RUNNING:
            return observation.state
        await asyncio.sleep(0.01)
    raise AssertionError("Codex test process did not reach a terminal observation")


@pytest.mark.asyncio
async def test_codex_turn_completed_is_reported_done_not_verified() -> None:
    executor = CodexExecExecutor(command=(sys.executable, "-u", "-c", _COMPLETED_SCRIPT))
    task = Task(id=new_task_id(), objective="Implement feature")
    session_id = await executor.start_task(task, "project context")

    try:
        state = await _wait_for_terminal(executor, session_id)
        observation = await executor.inspect(session_id)
        assert state is ExecutorState.REPORTED_DONE
        assert "independent verification" in observation.summary
    finally:
        await executor.terminate(session_id)


@pytest.mark.asyncio
async def test_codex_turn_failed_maps_to_failed() -> None:
    executor = CodexExecExecutor(command=(sys.executable, "-u", "-c", _FAILED_SCRIPT))
    task = Task(id=new_task_id(), objective="Implement feature")
    session_id = await executor.start_task(task, "project context")

    try:
        state = await _wait_for_terminal(executor, session_id)
        assert state is ExecutorState.FAILED
    finally:
        await executor.terminate(session_id)


@pytest.mark.asyncio
async def test_codex_malformed_jsonl_fails_closed() -> None:
    executor = CodexExecExecutor(command=(sys.executable, "-u", "-c", _MALFORMED_SCRIPT))
    task = Task(id=new_task_id(), objective="Implement feature")
    session_id = await executor.start_task(task, "project context")

    try:
        state = await _wait_for_terminal(executor, session_id)
        observation = await executor.inspect(session_id)
        assert state is ExecutorState.FAILED
        assert "malformed JSONL" in observation.summary
    finally:
        await executor.terminate(session_id)
