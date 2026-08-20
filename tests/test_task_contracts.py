from __future__ import annotations

import pytest

from epipilot.core.contracts import AcceptanceCommand, TaskContract
from epipilot.core.models import new_task_id


def test_task_contract_requires_revision_and_independent_acceptance() -> None:
    task_id = new_task_id()
    contract = TaskContract(
        task_id=task_id,
        repository_revision="abc123",
        allowed_write_paths=("src/**",),
        forbidden_paths=("evaluation/**",),
        acceptance=(AcceptanceCommand(name="tests", argv=("pytest", "-q")),),
    )

    assert contract.independently_verifiable


def test_task_contract_rejects_exact_write_forbidden_collision() -> None:
    with pytest.raises(ValueError, match="writable and forbidden"):
        TaskContract(
            task_id=new_task_id(),
            repository_revision="abc123",
            allowed_write_paths=("evaluation/config.py",),
            forbidden_paths=("evaluation/config.py",),
        )


def test_task_contract_rejects_repository_escape() -> None:
    with pytest.raises(ValueError, match="escape"):
        TaskContract(
            task_id=new_task_id(),
            repository_revision="abc123",
            allowed_write_paths=("../outside.txt",),
        )


def test_acceptance_command_uses_argv_not_shell_string() -> None:
    with pytest.raises(ValueError, match="argv"):
        AcceptanceCommand(name="tests", argv=())
