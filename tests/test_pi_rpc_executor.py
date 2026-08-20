from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest

from epipilot.core.models import Task, new_task_id
from epipilot.executors.base import ExecutorState
from epipilot.executors.pi_rpc import PiRpcExecutor

_AGENT_END_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    command = json.loads(sys.stdin.readline())
    response = {
        "type": "response",
        "id": command.get("id"),
        "command": "prompt",
        "success": True,
    }
    print(json.dumps(response), flush=True)
    print(json.dumps({"type": "agent_start"}), flush=True)
    print(json.dumps({"type": "agent_end"}), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("type") == "abort":
            break
    """
)

_INTERACTIVE_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    json.loads(sys.stdin.readline())
    request = {
        "type": "extension_ui_request",
        "id": "ui-1",
        "method": "confirm",
        "title": "Deploy to production?",
    }
    print(json.dumps(request), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("type") == "abort":
            break
    """
)


async def _wait_for_non_running(executor: PiRpcExecutor, session_id: str) -> ExecutorState:
    for _ in range(100):
        observation = await executor.inspect(session_id)
        if observation.state is not ExecutorState.RUNNING:
            return observation.state
        await asyncio.sleep(0.01)
    raise AssertionError("Pi RPC test agent did not reach a terminal observation")


@pytest.mark.asyncio
async def test_pi_rpc_maps_agent_end_to_reported_done_only() -> None:
    executor = PiRpcExecutor(command=(sys.executable, "-u", "-c", _AGENT_END_SCRIPT))
    task = Task(id=new_task_id(), objective="Implement feature")
    session_id = await executor.start_task(task, "project context")

    try:
        state = await _wait_for_non_running(executor, session_id)
        observation = await executor.inspect(session_id)
        assert state is ExecutorState.REPORTED_DONE
        assert "independent verification" in observation.summary
    finally:
        await executor.terminate(session_id)


@pytest.mark.asyncio
async def test_pi_rpc_interactive_confirm_blocks_instead_of_auto_answering() -> None:
    executor = PiRpcExecutor(command=(sys.executable, "-u", "-c", _INTERACTIVE_SCRIPT))
    task = Task(id=new_task_id(), objective="Deploy change")
    session_id = await executor.start_task(task, "project context")

    try:
        state = await _wait_for_non_running(executor, session_id)
        observation = await executor.inspect(session_id)
        assert state is ExecutorState.BLOCKED
        assert "interactive input" in observation.summary
    finally:
        await executor.terminate(session_id)
