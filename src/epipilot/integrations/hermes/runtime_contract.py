"""Shared runtime contract between the Hermes frontend plugin and executor adapter."""

from __future__ import annotations

from typing import Final

EXECUTOR_CHILD_ENV: Final = "EPIPILOT_HERMES_EXECUTOR_CHILD"
EXECUTOR_WORKSPACE_ENV: Final = "EPIPILOT_HERMES_WORKSPACE_ROOT"
EXECUTOR_TOOLSET: Final = "epipilot_executor"
EXECUTOR_GUARD_TOOL: Final = "epipilot_executor_guard"

# The bounded HermesExecutor currently exposes only Hermes' native file toolset.
# Terminal/process execution remains owned by EpiPilot's independent verifier until
# TaskContract command/resource enforcement is wired into the executor boundary.
EXECUTOR_ALLOWED_NATIVE_TOOLS: Final[frozenset[str]] = frozenset(
    {"read_file", "search_files", "write_file", "patch"}
)
