"""Verify immutable host-captured source notes, not the scientific claims in them."""

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from epipilot.research.discovery import validate_relative_path
from epipilot.research.intelligence_models import SourceSnapshot


def validate_source_snapshot(output: Path, source: SourceSnapshot) -> None:
    url = urlsplit(source.url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("source requires public URL without credentials")
    validate_relative_path(source.artifact)
    path = output / source.artifact
    if output.resolve() not in path.resolve().parents or path.is_symlink():
        raise ValueError("source artifact escapes output directory")
    if path.stat().st_size > 1_000_000:
        raise ValueError("source note exceeds bounded capture size")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source.sha256:
        raise ValueError("source artifact hash mismatch")
    lines = raw.decode("utf8").splitlines()
    if source.line > len(lines) or source.quote != lines[source.line - 1]:
        raise ValueError("source quote does not match captured note")
