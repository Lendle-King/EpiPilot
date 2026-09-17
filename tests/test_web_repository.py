from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from epipilot.web.repository import RepositoryInspector


def make_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("# Example project\n\nA source-linked project.\n")
    (path / "src").mkdir()
    (path / "src" / "engine.py").write_text('"""Coordinates project execution."""\nimport json\n')
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.org",
         "commit", "-qm", "fixture"], check=True,
    )
    return path


def test_reads_committed_metadata_not_dirty_working_tree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    (repo / "README.md").write_text("uncommitted text must not become pinned evidence")
    view = RepositoryInspector(repo).inspect()
    assert view.dirty
    assert len(view.revision) == 40
    assert "source-linked" in view.documents[0].text
    assert "uncommitted text" not in view.documents[0].text
    assert view.modules[0].summary == "Coordinates project execution."


def test_excludes_untracked_secrets_and_symlinks(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    (repo / ".env").write_text("SECRET=must-not-appear")
    (repo / "credentials.py").write_text("SECRET=must-not-appear")
    (repo / "docs").mkdir()
    (repo / "docs" / "leak.md").symlink_to(repo / ".env")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.org", "commit", "-qm", "safety"], check=True)
    result = RepositoryInspector(repo).inspect().model_dump_json()
    assert "must-not-appear" not in result
    assert "leak.md" not in result
    assert "credentials.py" not in result


def test_no_commit_is_an_error_not_an_empty_success(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(ValueError, match="commit"):
        RepositoryInspector(tmp_path).inspect()


def test_limits_are_visible(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    view = RepositoryInspector(repo, max_files=1).inspect()
    assert view.truncated
    assert view.scanned_files == 1
    assert view.total_files >= 2
