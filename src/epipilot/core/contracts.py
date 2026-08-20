"""Bounded execution contracts for coding-agent tasks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from epipilot.core.models import TaskId


class ResourceKind(StrEnum):
    """Resource classes that may require scheduler ownership or accounting."""

    CPU = "cpu"
    GPU = "gpu"
    MEMORY = "memory"
    PORT = "port"
    WORKTREE = "worktree"
    DATASET = "dataset"
    SERVICE = "service"
    API_BUDGET = "api_budget"


@dataclass(frozen=True, slots=True)
class ResourceClaim:
    """Explicit resource requested by a task."""

    kind: ResourceKind
    key: str
    quantity: float = 1.0
    exclusive: bool = False

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("resource key must not be empty")
        if self.quantity <= 0:
            raise ValueError("resource quantity must be positive")


@dataclass(frozen=True, slots=True)
class AcceptanceCommand:
    """Deterministic argv-based acceptance check executed outside the executor."""

    name: str
    argv: tuple[str, ...]
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("acceptance command name must not be empty")
        if not self.argv or any(not item for item in self.argv):
            raise ValueError("acceptance argv must contain non-empty entries")
        if self.timeout_seconds <= 0:
            raise ValueError("acceptance timeout must be positive")


@dataclass(frozen=True, slots=True)
class TaskContract:
    """Execution boundary supplied to a coding-agent task.

    Paths are repository-relative normalized strings or glob-like policy expressions.
    Enforcement belongs to workspace/sandbox adapters; this value object makes the
    intended authority explicit and auditable before execution begins.
    """

    task_id: TaskId
    repository_revision: str
    precondition_tasks: tuple[TaskId, ...] = ()
    allowed_read_paths: tuple[str, ...] = ()
    allowed_write_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    resources: tuple[ResourceClaim, ...] = ()
    acceptance: tuple[AcceptanceCommand, ...] = ()

    def __post_init__(self) -> None:
        if not self.repository_revision.strip():
            raise ValueError("task contract requires a repository revision")
        if self.task_id in self.precondition_tasks:
            raise ValueError("task cannot list itself as a precondition")

        for field_name, values in (
            ("allowed_read_paths", self.allowed_read_paths),
            ("allowed_write_paths", self.allowed_write_paths),
            ("forbidden_paths", self.forbidden_paths),
            ("expected_outputs", self.expected_outputs),
        ):
            _validate_policy_values(field_name, values)

        exact_write_forbidden = set(self.allowed_write_paths) & set(self.forbidden_paths)
        if exact_write_forbidden:
            raise ValueError("the same path cannot be both writable and forbidden")

        resource_keys = [(claim.kind, claim.key) for claim in self.resources]
        if len(set(resource_keys)) != len(resource_keys):
            raise ValueError("resource claims must have unique kind/key pairs")

        acceptance_names = [check.name for check in self.acceptance]
        if len(set(acceptance_names)) != len(acceptance_names):
            raise ValueError("acceptance check names must be unique")

    @property
    def independently_verifiable(self) -> bool:
        """Return whether the task declares at least one deterministic acceptance check."""
        return bool(self.acceptance)


def _validate_policy_values(name: str, values: tuple[str, ...]) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} entries must not be empty")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} entries must be unique")
    for value in values:
        if value.startswith("/"):
            raise ValueError(f"{name} entries must be repository-relative")
        if ".." in value.split("/"):
            raise ValueError(f"{name} entries must not escape the repository")
