"""Fast, read-only repository inventory for agent-guided research onboarding."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from epipilot.research.models import OnboardingSnapshot

_SKIP_DIRS = {
    ".git", ".epipilot", ".research", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", "dist", "build", "artifacts",
}
_SUFFIXES = {".py", ".md", ".toml", ".json", ".yaml", ".yml", ".ipynb", ".csv"}
_RELEVANT = ("readme", "research", "experiment", "claim", "result", "report", "benchmark", "backtest")


def git_revision(root: Path) -> str:
    """Resolve the current Git revision without running a shell."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unversioned"
    return result.stdout.strip() if result.returncode == 0 else "unversioned"


def inspect_repository(root: Path, *, max_files: int = 10000) -> OnboardingSnapshot:
    """Create a bounded manifest; never infer validated scientific claims from filenames."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("repository directory does not exist")
    counts: dict[str, int] = {}
    relevant: list[str] = []
    scanned = 0
    for directory, subdirs, filenames in os.walk(root):
        subdirs[:] = sorted(name for name in subdirs if name not in _SKIP_DIRS)
        for filename in sorted(filenames):
            if scanned >= max_files:
                raise ValueError("repository inventory exceeded max_files; narrow the project root")
            if filename.startswith(".env") or filename.endswith((".pem", ".key")):
                continue
            path = Path(directory) / filename
            suffix = path.suffix.lower()
            if suffix not in _SUFFIXES:
                continue
            scanned += 1
            counts[suffix] = counts.get(suffix, 0) + 1
            relative = path.relative_to(root).as_posix()
            if any(term in relative.lower() for term in _RELEVANT):
                relevant.append(relative)
    return OnboardingSnapshot(
        git_revision=git_revision(root),
        project_root=str(root),
        source_counts=dict(sorted(counts.items())),
        relevant_files=relevant[:200],
    )
