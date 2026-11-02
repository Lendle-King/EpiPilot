"""Public, synthetic calibration only; never use these seeds as a hidden test."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Literal

Candidate = Literal["baseline", "reference"]


def progress_area(budget: float, events: Sequence[tuple[float, float]]) -> float:
    """Integrate validated normalized scores in observation-time order."""
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("budget must be finite and positive")
    previous = 0.0
    best = 0.0
    area = 0.0
    for elapsed, quality in events:
        if not math.isfinite(elapsed) or not previous <= elapsed <= budget:
            raise ValueError("timestamps must be finite, ordered and within budget")
        if not math.isfinite(quality) or not 0 <= quality <= 1:
            raise ValueError("quality must be finite and in [0, 1]")
        area += (elapsed - previous) * best
        previous = elapsed
        best = max(best, quality)
    return (area + (budget - previous) * best) / budget


def _check(candidate: str) -> None:
    if candidate not in ("baseline", "reference"):
        raise ValueError("unknown calibration candidate")


def scheduling_loss(seed: int, candidate: Candidate) -> float:
    """Weighted tardiness in a single-server queue with stochastic arrivals."""
    _check(candidate)
    rng = random.Random(seed)
    arrival = 0.0
    jobs: list[tuple[float, float, float, float]] = []
    for _ in range(160):
        arrival += rng.expovariate(1.0 / 1.1)
        duration = rng.uniform(0.3, 1.8)
        deadline = arrival + rng.uniform(1.5, 5.0)
        weight = 8.0 if rng.random() < 0.25 else 1.0
        jobs.append((arrival, duration, deadline, weight))
    pending: list[tuple[float, float, float, float]] = []
    now = 0.0
    i = 0
    total = 0.0
    while i < len(jobs) or pending:
        if not pending:
            now = max(now, jobs[i][0])
        while i < len(jobs) and jobs[i][0] <= now:
            pending.append(jobs[i])
            i += 1
        if candidate == "baseline":
            index = min(range(len(pending)), key=lambda j: pending[j][2])
        else:
            index = min(range(len(pending)), key=lambda j: pending[j][1] / pending[j][3])
        _, duration, deadline, weight = pending.pop(index)
        now += duration
        total += weight * max(0.0, now - deadline)
    return total / len(jobs)


def classification_loss(seed: int, candidate: Candidate) -> float:
    """Error under shifted nuisance-label correlation in a synthetic population."""
    _check(candidate)
    rng = random.Random(seed)
    errors = 0
    for _ in range(2000):
        label = rng.choice((-1, 1))
        stable = label + rng.gauss(0, 0.8)
        nuisance = -0.65 * label + rng.gauss(0, 0.8)
        score = stable + (1.5 * nuisance if candidate == "baseline" else 0.0)
        errors += (score >= 0) != (label == 1)
    return errors / 2000
