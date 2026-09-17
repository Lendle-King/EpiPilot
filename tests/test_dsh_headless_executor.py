from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest
from epipilot.executors.dsh_headless import DshHeadlessExecutor

from epipilot.core.models import Task, new_task_id
from epipilot.executors.base import ExecutorState

_FINAL_SUCCESS_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    prompt = sys.argv[-1]
    assert "project context" in prompt
    assert "Implement feature" in prompt
    print(json.dumps({"type": "session", "session_id": "session-1"}), flush=True)
    print(json.dumps({"type": "final", "text": "done", "turn_end": "completed"}), flush=True)
    """
)

_FINAL_FAILURE_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    print(json.dumps({"type": "final", "text": "not done", "turn_end": "error"}), flush=True)
    sys.exit(1)
    """
)

_ERROR_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    print(json.dumps({"type": "error", "message": "startup failed"}), flush=True)
    sys.exit(1)
    """
)

_MISSING_FINAL_SCRIPT = textwrap.dedent(
    """
    import json

    print(json.dumps({"type": "status", "status": "idle"}), flush=True)
    """
)


async def _wait_for_terminal(executor: DshHeadlessExecutor, session_id: str) -> ExecutorState:
    for _ in range(100):
        observation = await executor.inspect(session_id)
        if observation.state is not ExecutorState.RUNNING:
            return observation.state
        await asyncio.sleep(0.01)
    raise AssertionError("DSH test process did not reach a terminal observation")


@pytest.mark.asyncio
async def test_dsh_requires_final_and_zero_exit_for_reported_done() -> None:
    executor = DshHeadlessExecutor(command=(sys.executable, "-u", "-c", _FINAL_SUCCESS_SCRIPT))
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
async def test_dsh_final_with_nonzero_exit_fails() -> None:
    executor = DshHeadlessExecutor(command=(sys.executable, "-u", "-c", _FINAL_FAILURE_SCRIPT))
    task = Task(id=new_task_id(), objective="Implement feature")
    session_id = await executor.start_task(task, "project context")

    try:
        state = await _wait_for_terminal(executor, session_id)
        assert state is ExecutorState.FAILED
    finally:
        await executor.terminate(session_id)


@pytest.mark.asyncio
async def test_dsh_error_event_fails() -> None:
    executor = DshHeadlessExecutor(command=(sys.executable, "-u", "-c", _ERROR_SCRIPT))
    task = Task(id=new_task_id(), objective="Implement feature")
    session_id = await executor.start_task(task, "project context")

    try:
        state = await _wait_for_terminal(executor, session_id)
        assert state is ExecutorState.FAILED
    finally:
        await executor.terminate(session_id)


@pytest.mark.asyncio
async def test_dsh_zero_exit_without_final_fails_closed() -> None:
    executor = DshHeadlessExecutor(command=(sys.executable, "-u", "-c", _MISSING_FINAL_SCRIPT))
    task = Task(id=new_task_id(), objective="Implement feature")
    session_id = await executor.start_task(task, "project context")

    try:
        state = await _wait_for_terminal(executor, session_id)
        observation = await executor.inspect(session_id)
        assert state is ExecutorState.FAILED
        assert "without final" in observation.summary
    finally:
        await executor.terminate(session_id)
