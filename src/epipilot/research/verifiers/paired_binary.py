"""Recompute matched binary-outcome metrics from raw paired evaluation records.

This verifies data consistency and a predeclared pairing identity, not that
the task split was held out, trials were independent, or a causal effect exists.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


class InvalidPairedResult(ValueError):
    """An experiment result has incomplete, inconsistent, or invalid paired data."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise InvalidPairedResult(f"duplicate JSON field: {key}")
        data[key] = value
    return data


def _numeric(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidPairedResult(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidPairedResult(f"{name} must be finite")
    return number


def exact_mcnemar_p(wins: int, losses: int) -> float:
    """Two-sided exact binomial p-value for discordant matched binary outcomes."""
    if wins < 0 or losses < 0:
        raise ValueError("discordant counts must be nonnegative")
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, k) for k in range(min(wins, losses) + 1))
    return min(1.0, (2 * tail) / (1 << discordant))


def verify_paired_binary(
    artifact: Path, *, min_pairs: int = 8, expected_split: str | None = None
) -> dict[str, int | float | str]:
    """Validate raw matched outcomes and recalculate all published metrics."""
    if not 1 <= min_pairs <= 4096:
        raise InvalidPairedResult("min_pairs must lie within [1,4096]")
    if not artifact.is_file() or artifact.stat().st_size > 8 * 1024 * 1024:
        raise InvalidPairedResult("paired metric artifact missing or exceeds 8 MiB")
    try:
        data = json.loads(
            artifact.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda val: (_ for _ in ()).throw(
                InvalidPairedResult(f"nonfinite JSON value: {val}")
            ),
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise InvalidPairedResult("paired artifact must be valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise InvalidPairedResult("paired artifact must be an object")
    protocol = data.get("protocol")
    if (
        not isinstance(protocol, dict)
        or not isinstance(protocol.get("split"), str)
        or not protocol["split"].strip()
    ):
        raise InvalidPairedResult("protocol.split must identify the evaluation split")
    split = protocol["split"]
    if expected_split is not None and split != expected_split:
        raise InvalidPairedResult("paired data split does not match the frozen verifier")

    pairs = data.get("pairs")
    if not isinstance(pairs, list) or not min_pairs <= len(pairs) <= 4096:
        raise InvalidPairedResult("incorrect or insufficient paired sample count")
    seen: set[str] = set()
    wins = losses = baseline_count = candidate_count = 0
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {"id", "baseline", "candidate"}:
            raise InvalidPairedResult("pair needs exact id, baseline, candidate fields")
        pair_id = pair["id"]
        if not isinstance(pair_id, str) or not pair_id.strip() or pair_id in seen:
            raise InvalidPairedResult("pair id missing or duplicated")
        seen.add(pair_id)
        baseline, candidate = pair["baseline"], pair["candidate"]
        if not isinstance(baseline, bool) or not isinstance(candidate, bool):
            raise InvalidPairedResult("paired outcomes must be booleans")
        baseline_count += int(baseline)
        candidate_count += int(candidate)
        wins += int(not baseline and candidate)
        losses += int(baseline and not candidate)

    measured = {
        "delta_success": (wins - losses) / len(pairs),
        "paired_p_value": exact_mcnemar_p(wins, losses),
        "pair_count": float(len(pairs)),
    }
    metrics = data.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(measured):
        raise InvalidPairedResult("metrics must contain exactly the recomputable paired fields")
    for key, correct in measured.items():
        published = _numeric(metrics[key], key)
        if not math.isclose(published, correct, rel_tol=0.0, abs_tol=1e-9):
            raise InvalidPairedResult(f"published {key} differs from raw paired outcomes")
    return {
        "split": split,
        "pairs": len(pairs),
        "wins": wins,
        "losses": losses,
        "baseline_successes": baseline_count,
        "candidate_successes": candidate_count,
        "delta_success": measured["delta_success"],
        "paired_p_value": measured["paired_p_value"],
        "qualification": (
            "Data-consistency check only: not proof of matched initial conditions, "
            "independent samples, no leakage, or causal validity"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--min-pairs", type=int, default=8)
    parser.add_argument("--expected-split")
    args = parser.parse_args(argv)
    try:
        summary = verify_paired_binary(
            args.artifact, min_pairs=args.min_pairs, expected_split=args.expected_split
        )
    except (InvalidPairedResult, OSError, OverflowError) as exc:
        print(f"paired verification rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
