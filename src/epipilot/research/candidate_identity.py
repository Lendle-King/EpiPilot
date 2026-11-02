"""Stable structural identity for candidate proposals and configurations.

Normalizes only JSON-equivalent finite numeric values (e.g. 1 vs 1.0).
Domain-specific equivalences such as redundant features require a separate,
explicitly justified and versioned task-specific canonicalizer.
"""

from __future__ import annotations

import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any


def normalize_candidate(value: Any) -> Any:
    """Recursively canonicalize JSON-like candidate data without changing its semantics."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("candidate keys must be strings")
        return {key: normalize_candidate(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [normalize_candidate(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("candidate numeric values must be finite")
        if value.is_integer() and abs(value) <= 2**53:
            return int(value)
        return value
    raise ValueError("candidate must contain JSON-compatible values")


def candidate_fingerprint(candidate: dict[str, Any]) -> str:
    """Hash a canonical representation; not a claim of scientifically equal outcomes."""
    canonical = normalize_candidate(candidate)
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate candidate JSON property: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid candidate numeric constant: {value}")


def _read_candidate_json(root: Path, path: Path, max_bytes: int) -> Any:
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("candidate input must stay inside research root")
    if resolved.stat().st_size > max_bytes:
        raise ValueError("candidate input exceeds size limit")
    return json.loads(
        resolved.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


def load_candidate(root: Path, path: Path) -> dict[str, Any]:
    """Read one bounded JSON candidate without allowing a path escape."""
    data = _read_candidate_json(root, path, 256 * 1024)
    if not isinstance(data, dict):
        raise ValueError("candidate must be a JSON object")
    normalize_candidate(data)
    return data


def screen_candidate(root: Path, candidate_path: Path, history_path: Path) -> dict[str, object]:
    """Determine whether candidate is already present in a local configuration history."""
    current = load_candidate(root, candidate_path)
    history = _read_candidate_json(root, history_path, 2 * 1024 * 1024)
    if not isinstance(history, list) or len(history) > 10000:
        raise ValueError("candidate history must be a bounded JSON array")
    if any(not isinstance(item, dict) for item in history):
        raise ValueError("candidate history entries must be JSON objects")
    current_key = candidate_fingerprint(current)
    duplicate_indices = [
        index for index, item in enumerate(history) if candidate_fingerprint(item) == current_key
    ]
    return {
        "fingerprint": current_key,
        "duplicate": bool(duplicate_indices),
        "duplicate_indices": duplicate_indices,
        "history_count": len(history),
        "qualification": (
            "Only structural JSON/numeric equivalence is checked. "
            "The agent must still review domain-specific semantic aliases and "
            "the actual historical experiment receipts before spending resources."
        ),
    }
