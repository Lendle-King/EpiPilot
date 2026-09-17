from __future__ import annotations

from pathlib import Path

import pytest

from epipilot.core.models import Task
from epipilot.executors.base import ExecutorObservation, ExecutorState
from epipilot.executors.codex_exec import CodexExecExecutor
from epipilot.executors.dsh_headless import DshHeadlessExecutor
from epipilot.executors.pi_rpc import PiRpcExecutor
from epipilot.executors.registry import (
    ExecutorConfig,
    ExecutorRegistry,
    create_executor,
    default_executor_registry,
)


class _DummyExecutor:
    async def start_task(self, task: Task, context: str) -> str:
        return "dummy-session"

    async def inspect(self, session_id: str) -> ExecutorObservation:
        return ExecutorObservation(ExecutorState.REPORTED_DONE, "dummy reported done")

    async def interrupt(self, session_id: str, reason: str) -> None:
        return None

    async def terminate(self, session_id: str) -> None:
        return None


def test_executor_config_rejects_empty_backend() -> None:
    with pytest.raises(ValueError, match="backend"):
        ExecutorConfig(backend="   ")


def test_default_registry_exposes_builtin_backends() -> None:
    assert default_executor_registry().names() == ("codex", "dsh", "pi")


def test_create_executor_selects_builtin_backend_and_preserves_options() -> None:
    cwd = Path("/tmp/project")
    command = ("custom-agent", "--json")

    codex = create_executor(
        ExecutorConfig(
            backend=" CODEX ",
            cwd=cwd,
            command=command,
            shutdown_timeout_seconds=7.0,
        )
    )
    dsh = create_executor(ExecutorConfig(backend="dsh", cwd=cwd, command=command))
    pi = create_executor(ExecutorConfig(backend="pi", cwd=cwd, command=command))

    assert isinstance(codex, CodexExecExecutor)
    assert codex.cwd == cwd
    assert codex.command == command
    assert codex.shutdown_timeout_seconds == 7.0
    assert isinstance(dsh, DshHeadlessExecutor)
    assert dsh.cwd == cwd
    assert dsh.command == command
    assert isinstance(pi, PiRpcExecutor)
    assert pi.cwd == cwd
    assert pi.command == command


def test_registry_supports_custom_backend_and_rejects_duplicate() -> None:
    registry = ExecutorRegistry()
    registry.register("custom", lambda config: _DummyExecutor())

    executor = registry.create(ExecutorConfig(backend="CUSTOM"))
    assert isinstance(executor, _DummyExecutor)
    assert registry.names() == ("custom",)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("custom", lambda config: _DummyExecutor())


def test_registry_unknown_backend_lists_available_names() -> None:
    registry = default_executor_registry()

    with pytest.raises(ValueError, match="codex, dsh, pi"):
        registry.create(ExecutorConfig(backend="unknown"))
