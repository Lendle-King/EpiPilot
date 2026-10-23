"""Local-only artifact and execution validation; no shell and no remote service."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from epipilot.research.models import ExperimentContract
from epipilot.research.onboarding import git_revision

_MAX_METRIC_BYTES = 8 * 1024 * 1024


def _in_root(root: Path, relative: str) -> Path:
    """Do not follow an artifact path outside the research repository."""
    base = root.resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError("artifact path does not refer to a regular file inside the repository")
    return path


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def optional_fingerprint(root: Path, reference: str) -> str | None:
    """Hash existing repository-local config/data; external version IDs stay explicit."""
    candidate = Path(reference)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("config/dataset references must be relative paths or version IDs")
    path = root / candidate
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("config/dataset reference is not a regular file")
    return hash_file(_in_root(root, reference))


def require_clean_revision(root: Path, expected: str | None = None) -> str:
    """Require pinned committed source; ignore generated, untracked result artifacts."""
    revision = git_revision(root)
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise ValueError("research repository requires a valid committed Git HEAD")
    if expected is not None and revision != expected:
        raise ValueError("Git HEAD differs from the frozen experiment revision")
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("could not inspect tracked Git worktree") from exc
    if result.returncode != 0 or result.stdout.strip():
        raise ValueError("commit tracked code/config changes before pre-registration or validation")
    return revision


def check_fingerprints(root: Path, contract: ExperimentContract) -> None:
    """Reject changed local config/data, including deletion of a previously present file."""
    for ref, old_digest in (
        (contract.config_ref, contract.config_sha256),
        (contract.dataset_ref, contract.dataset_sha256),
    ):
        if optional_fingerprint(root, ref) != old_digest:
            raise ValueError("frozen config or dataset has changed since pre-registration")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def artifact_metrics(root: Path, contract: ExperimentContract) -> tuple[dict[str, float], str]:
    """Read machine-checkable metrics independently of an agent's reported values."""
    path = _in_root(root, contract.artifact_ref)
    if path.stat().st_size > _MAX_METRIC_BYTES:
        raise ValueError("metric artifact exceeds the maximum supported size")
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"invalid JSON {v}")),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("metric artifact must contain valid UTF-8 JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), dict):
        raise ValueError("metric artifact must be a JSON object with a metrics object")
    values = data["metrics"]
    if set(values) != {rule.name for rule in contract.metric_rules}:
        raise ValueError("artifact metric names disagree with frozen contract")
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in values.values()
    ):
        raise ValueError("artifact metrics must be finite numeric values")
    return {key: float(value) for key, value in values.items()}, hash_file(path)


def run_frozen_verifier(
    root: Path, contract: ExperimentContract, timeout: int
) -> tuple[bool, str, int, list[str]]:
    """Run only the pre-registered argv. Hash stdout/stderr, never persist raw output."""
    if timeout < 1 or timeout > 3600:
        raise ValueError("verification timeout must be between 1 and 3600 seconds")
    argv = [value.replace("{artifact}", contract.artifact_ref) for value in contract.verifier_argv]
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                argv,
                cwd=root,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout,
            )
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
        except OSError as exc:
            raise ValueError(f"cannot execute frozen verifier: {exc}") from exc
        output.seek(0)
        digest = hashlib.sha256()
        for chunk in iter(lambda: output.read(1024 * 1024), b""):
            digest.update(chunk)
    return code == 0, digest.hexdigest(), code, argv
