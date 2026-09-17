"""Configuration-driven selection of replaceable coding-agent executors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from epipilot.executors.base import CodingAgentExecutor
from epipilot.executors.codex_exec import CodexExecExecutor
from epipilot.executors.dsh_headless import DshHeadlessExecutor
from epipilot.executors.pi_rpc import PiRpcExecutor


@dataclass(frozen=True, slots=True)
class ExecutorConfig:
    """Runtime configuration shared by built-in executor adapters."""

    backend: str
    cwd: Path | None = None
    command: tuple[str, ...] | None = None
    shutdown_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.backend.strip():
            raise ValueError("executor backend must not be empty")
        if self.command is not None and (
            not self.command or any(not part for part in self.command)
        ):
            raise ValueError("executor command must contain non-empty argv entries")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown timeout must be positive")


ExecutorFactory = Callable[[ExecutorConfig], CodingAgentExecutor]


@dataclass(slots=True)
class ExecutorRegistry:
    """Map stable backend names to executor factories."""

    _factories: dict[str, ExecutorFactory] = field(default_factory=dict)

    def register(
        self,
        name: str,
        factory: ExecutorFactory,
        *,
        replace: bool = False,
    ) -> None:
        normalized = _normalize_name(name)
        if normalized in self._factories and not replace:
            raise ValueError(f"executor backend already registered: {normalized}")
        self._factories[normalized] = factory

    def create(self, config: ExecutorConfig) -> CodingAgentExecutor:
        normalized = _normalize_name(config.backend)
        try:
            factory = self._factories[normalized]
        except KeyError as error:
            available = ", ".join(self.names()) or "none"
            raise ValueError(
                f"unknown executor backend {normalized!r}; available: {available}"
            ) from error
        return factory(config)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


def default_executor_registry() -> ExecutorRegistry:
    """Return a fresh registry containing all built-in executor adapters."""
    registry = ExecutorRegistry()
    registry.register("pi", _make_pi)
    registry.register("codex", _make_codex)
    registry.register("dsh", _make_dsh)
    return registry


def create_executor(config: ExecutorConfig) -> CodingAgentExecutor:
    """Construct a built-in executor from runtime configuration."""
    return default_executor_registry().create(config)


def _make_pi(config: ExecutorConfig) -> CodingAgentExecutor:
    if config.command is None:
        return PiRpcExecutor(
            cwd=config.cwd,
            shutdown_timeout_seconds=config.shutdown_timeout_seconds,
        )
    return PiRpcExecutor(
        command=config.command,
        cwd=config.cwd,
        shutdown_timeout_seconds=config.shutdown_timeout_seconds,
    )


def _make_codex(config: ExecutorConfig) -> CodingAgentExecutor:
    if config.command is None:
        return CodexExecExecutor(
            cwd=config.cwd,
            shutdown_timeout_seconds=config.shutdown_timeout_seconds,
        )
    return CodexExecExecutor(
        command=config.command,
        cwd=config.cwd,
        shutdown_timeout_seconds=config.shutdown_timeout_seconds,
    )


def _make_dsh(config: ExecutorConfig) -> CodingAgentExecutor:
    if config.command is None:
        return DshHeadlessExecutor(
            cwd=config.cwd,
            shutdown_timeout_seconds=config.shutdown_timeout_seconds,
        )
    return DshHeadlessExecutor(
        command=config.command,
        cwd=config.cwd,
        shutdown_timeout_seconds=config.shutdown_timeout_seconds,
    )


def _normalize_name(name: str) -> str:
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("executor backend name must not be empty")
    return normalized
