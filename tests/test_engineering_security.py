import subprocess
from pathlib import Path

import pytest

from epipilot.engineering.models import ManagedPolicy
from epipilot.engineering.reports import deliver_report
from epipilot.engineering.service import ManagedWork
from epipilot.runtime.sqlite_event_store import SqliteEventStore


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def setup(tmp_path: Path, *, worktree_root: Path | None = None) -> tuple[ManagedWork, Path]:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("base\n")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "base")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(root, "remote", "add", "origin", str(remote))
    service = ManagedWork(SqliteEventStore(tmp_path / "events.sqlite3"), "security", root)
    managed_root = worktree_root or (tmp_path / "managed")
    service.configure(
        ManagedPolicy(
            repository=str(root),
            common_dir=str(root / ".git"),
            worktree_root=str(managed_root),
            remote="origin",
            remote_url=str(remote),
            publication_ref="refs/heads/delivery",
            acceptance=(("python", "-c", "pass"),),
        )
    )
    return service, root


def test_report_preserves_trailing_newline(tmp_path: Path) -> None:
    service, root = setup(tmp_path)
    markdown = "# Report\n\nResult.\n"
    path, commit = deliver_report(
        service.store.path, service.project_id, root, "report:trailing", markdown
    )
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path}"],
        capture_output=True,
        check=True,
    )
    assert result.stdout.decode() == markdown


def test_symlinked_worktree_root_rejected(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "managed-link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        setup(tmp_path / "nested", worktree_root=link)


def test_symlinked_task_path_rejected(tmp_path: Path) -> None:
    service, root = setup(tmp_path)
    policy = service.policy
    (Path(policy.worktree_root)).mkdir(parents=True, exist_ok=True)
    import hashlib

    operation = "task-link"
    key = hashlib.sha256((service.project_id + ":" + operation).encode()).hexdigest()[:24]
    target = Path(policy.worktree_root) / key
    target.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        service.acquire(operation, ("README.md",))


def test_worktree_from_other_repository_rejected(tmp_path: Path) -> None:
    service, _root = setup(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-b", "main")
    git(other, "config", "user.email", "test@example.invalid")
    git(other, "config", "user.name", "Test")
    (other / "x").write_text("x\n")
    git(other, "add", "x")
    git(other, "commit", "-m", "other")
    path = Path(service.policy.worktree_root) / "foreign"
    git(other, "worktree", "add", "--detach", str(path), "HEAD")
    import hashlib

    operation = "foreign"
    key = hashlib.sha256((service.project_id + ":" + operation).encode()).hexdigest()[:24]
    path.rename(Path(service.policy.worktree_root) / key)
    with pytest.raises(ValueError, match="another Git repository"):
        service.acquire(operation, ("README.md",))
