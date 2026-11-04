import subprocess
from pathlib import Path

import pytest

from epipilot.benchfirst.adapters import Source, check_source


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_source_pin_origin_dirty_and_ignored_inputs(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Synthetic")
    git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    git(tmp_path, "remote", "add", "origin", "https://github.com/example/synthetic")
    (tmp_path / ".gitignore").write_text("*.env\n")
    git(tmp_path, "add", ".gitignore")
    git(tmp_path, "commit", "-qm", "synthetic fixture")
    source = Source(
        checkout=tmp_path,
        sha=git(tmp_path, "rev-parse", "HEAD"),
        url="https://github.com/example/synthetic",
    )
    check_source(source)
    with pytest.raises(ValueError, match="identity"):
        check_source(source.model_copy(update={"sha": "f" * 40}))
    with pytest.raises(ValueError, match="identity"):
        check_source(source.model_copy(update={"url": "https://github.com/other/source"}))
    (tmp_path / "ignored.env").write_text("SYNTHETIC_NOT_A_SECRET")
    with pytest.raises(ValueError, match="dirty"):
        check_source(source)

    (tmp_path / "ignored.env").unlink()
    (tmp_path / ".gitignore").write_text("changed\n")
    with pytest.raises(ValueError, match="dirty"):
        check_source(source)
    (tmp_path / ".gitignore").write_text("*.env\n")
    (tmp_path / "unexpected.py").write_text("pass\n")
    with pytest.raises(ValueError, match="dirty"):
        check_source(source)
