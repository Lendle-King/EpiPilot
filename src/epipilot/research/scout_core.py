"""Agent-supplied Scout discoveries with immutable Git provenance and budget epochs."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from epipilot.research.scout_models import ScoutSource

if TYPE_CHECKING:
    from epipilot.research.models import ResearchState


def frontier_fingerprint(state: ResearchState) -> str:
    """No-op and failed searches cannot mint new epochs just by appending events."""
    payload = {
        "goal": None if state.goal is None else state.goal.model_dump(mode="json"),
        "unknowns": [
            (item.id, item.question, item.status, item.resolution_evidence_ids)
            for item in sorted(state.unknowns.values(), key=lambda x: x.id)
        ],
        "claims": [
            (
                item.id, item.statement, item.scope, item.status.value,
                item.supporting_evidence_ids, item.contradicting_evidence_ids,
            )
            for item in sorted(state.claims.values(), key=lambda x: x.id)
        ],
        "proposals": [
            (item.id, item.title, item.status)
            for item in sorted(state.proposals.values(), key=lambda x: x.id)
        ],
        "validated_evidence": [
            (item.id, item.scientific_verdict, item.artifact_sha256)
            for item in sorted(state.evidence.values(), key=lambda x: x.id)
            if item.measurement_validated
        ],
        "onboarding_revision": (
            state.onboarding[-1].git_revision if state.onboarding else None
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _git(root: Path, *argv: str) -> bytes:
    """Read only local Git objects, with bounded execution and argv (not a shell)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *argv],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("could not read pinned Git source object") from exc
    if result.returncode != 0:
        raise ValueError("pinned source is unavailable in local Git")
    return result.stdout


def validate_source(root: Path, revision: str, source: ScoutSource) -> None:
    """Require a regular committed blob, exact line, and correct SHA-256."""
    record = _git(root, "ls-tree", "-z", revision, "--", source.path)
    items = [part for part in record.split(b"\0") if part]
    if len(items) != 1:
        raise ValueError("Scout source must refer to exactly one committed file")
    metadata, sep, name = items[0].partition(b"\t")
    pieces = metadata.split(b" ")
    if (
        not sep
        or name != source.path.encode("utf-8")
        or len(pieces) != 3
        or pieces[0] not in {b"100644", b"100755"}
        or pieces[1] != b"blob"
    ):
        raise ValueError("Scout source is not a regular committed text file")
    oid = pieces[2].decode("ascii")
    size = _git(root, "cat-file", "-s", oid)
    try:
        length = int(size.strip())
    except ValueError as exc:
        raise ValueError("Scout source has invalid Git object size") from exc
    if length > 128 * 1024:
        raise ValueError("Scout source exceeds maximum text size")
    content = _git(root, "cat-file", "blob", oid)
    if hashlib.sha256(content).hexdigest() != source.sha256:
        raise ValueError("Scout source SHA-256 differs from committed Git blob")
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("Scout source is not valid UTF-8") from exc
    if source.line > len(lines) or lines[source.line - 1] != source.exact_quote:
        raise ValueError("Scout quote does not exactly match pinned source line")
