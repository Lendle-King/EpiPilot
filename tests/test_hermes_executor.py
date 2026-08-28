from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from epipilot.core.models import Task, new_task_id
from epipilot.executors.base import ExecutorState
from epipilot.executors.hermes import HermesExecutor
from epipilot.integrations.hermes.runtime_contract import (
    EXECUTOR_CHILD_ENV,
    EXECUTOR_WORKSPACE_ENV,
)

_SECRET_CONTEXT = "private-epipilot-context-that-must-not-appear-in-argv"

_SUCCESS_SCRIPT = textwrap.dedent(
    f"""
    import json
    import os
    import stat
    import sys
    from pathlib import Path

    argv = sys.argv[1:]
    if "--query-file" not in argv:
        raise SystemExit(20)
    prompt_path = Path(argv[argv.index("--query-file") + 1])
    prompt = prompt_path.read_text(encoding="utf-8")
    if {_SECRET_CONTEXT!r} not in prompt:
        raise SystemExit(21)
    if {_SECRET_CONTEXT!r} in " ".join(argv):
        raise SystemExit(22)
    if os.name != "nt" and stat.S_IMODE(prompt_path.stat().st_mode) & 0o077:
        raise SystemExit(23)
    if os.environ.get({EXECUTOR_CHILD_ENV!r}) != "1":
        raise SystemExit(24)

    workspace = Path(os.environ[{EXECUTOR_WORKSPACE_ENV!r}])
    (workspace / "result.txt").write_text("bounded change\n", encoding="utf-8")
    (workspace / "child-argv.json").write_text(json.dumps(argv), encoding="utf-8")
    print("implemented the requested bounded change")
    """
)

_FAILURE_SCRIPT = textwrap.dedent(
    """
    import sys
    print("sensitive provider diagnostic should not become observation", file=sys.stderr)
    raise SystemExit(7)
    """
)

_SLEEP_SCRIPT = textwrap.dedent(
    """
    import time
    time.sleep(30)
    print("should not complete")
    """
)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "tests@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "EpiPilot Tests"], check=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "fixture"], check=True)


async def _wait_terminal(executor: HermesExecutor, session_id: str):
    for _ in range(200):
        observation = await executor.inspect(session_id)
        if observation.state is not ExecutorState.RUNNING:
            return observation
        await asyncio.sleep(0.01)
    raise AssertionError("Hermes test child did not reach a terminal observation")


@pytest.mark.asyncio
async def test_hermes_executor_uses_private_query_file_and_reports_changes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    executor = HermesExecutor(
        workspace=tmp_path,
        command=(sys.executable, "-u", "-c", _SUCCESS_SCRIPT),
    )
    task = Task(id=new_task_id(), objective="Implement bounded change")

    session_id = await executor.start_task(task, _SECRET_CONTEXT)
    try:
        observation = await _wait_terminal(executor, session_id)
        assert observation.state is ExecutorState.REPORTED_DONE
        assert "independent EpiPilot verification" in observation.summary
        assert set(observation.changed_files) == {"child-argv.json", "result.txt"}

        argv = json.loads((tmp_path / "child-argv.json").read_text(encoding="utf-8"))
        assert "--query-file" in argv
        assert "--ignore-rules" in argv
        assert "epipilot_executor,file" in argv
        assert _SECRET_CONTEXT not in " ".join(argv)
        prompt_path = Path(argv[argv.index("--query-file") + 1])
        if os.name != "nt":
            assert stat.S_IMODE(prompt_path.stat().st_mode) == 0o600
    finally:
        argv = json.loads((tmp_path / "child-argv.json").read_text(encoding="utf-8"))
        prompt_path = Path(argv[argv.index("--query-file") + 1])
        await executor.terminate(session_id)

    assert not prompt_path.exists()


@pytest.mark.asyncio
async def test_hermes_executor_requires_attributable_clean_workspace(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    executor = HermesExecutor(
        workspace=tmp_path,
        command=(sys.executable, "-u", "-c", _SUCCESS_SCRIPT),
    )
    task = Task(id=new_task_id(), objective="Do not start on dirty state")

    with pytest.raises(ValueError, match="clean workspace"):
        await executor.start_task(task, "context")


@pytest.mark.asyncio
async def test_hermes_executor_failure_does_not_promote_raw_stderr(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    executor = HermesExecutor(
        workspace=tmp_path,
        command=(sys.executable, "-u", "-c", _FAILURE_SCRIPT),
    )
    task = Task(id=new_task_id(), objective="Fail safely")

    session_id = await executor.start_task(task, "context")
    try:
        observation = await _wait_terminal(executor, session_id)
        assert observation.state is ExecutorState.FAILED
        assert observation.failure_signature == "hermes-exit-7"
        assert "sensitive provider diagnostic" not in observation.summary
    finally:
        await executor.terminate(session_id)


@pytest.mark.asyncio
async def test_hermes_executor_interrupt_maps_to_blocked(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    executor = HermesExecutor(
        workspace=tmp_path,
        command=(sys.executable, "-u", "-c", _SLEEP_SCRIPT),
        shutdown_timeout_seconds=1.0,
    )
    task = Task(id=new_task_id(), objective="Long-running task")

    session_id = await executor.start_task(task, "context")
    await executor.interrupt(session_id, "supervision budget exhausted")
    try:
        observation = await _wait_terminal(executor, session_id)
        assert observation.state is ExecutorState.BLOCKED
        assert observation.failure_signature == "hermes-interrupted"
        assert "supervision budget exhausted" in observation.summary
    finally:
        await executor.terminate(session_id)
