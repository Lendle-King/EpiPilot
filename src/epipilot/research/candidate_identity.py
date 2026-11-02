"""Stable structural identity for candidate proposals and configurations.

Normalizes only JSON-equivalent finite numeric values (e.g. 1 vs 1.0).
Domain-specific equivalences such as redundant features require a separate,
explicitly justified and versioned task-specific canonicalizer.
"""

from __future__ import annotations

import hashlib
import json
from math import isfinite
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
