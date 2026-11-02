"""Exploratory task prototypes. All data and references here are public."""

from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Example:
    stable: float
    nuisance: float
    label: int


def generate_examples(seed: int, count: int, correlation: float) -> tuple[Example, ...]:
    if count <= 0 or not -1 <= correlation <= 1:
        raise ValueError("invalid population")
    rng = random.Random(seed)
    examples: list[Example] = []
    for _ in range(count):
        label = rng.choice((-1, 1))
        stable = label + rng.gauss(0, 1.0)
        nuisance = correlation * label + rng.gauss(0, 0.4)
        examples.append(Example(stable, nuisance, label))
    return tuple(examples)


def fit_classifier(examples: tuple[Example, ...], *, robust: bool) -> tuple[float, float]:
    """Train a regularized logistic classifier using deterministic gradient descent."""
    if not examples or any(x.label not in (-1, 1) for x in examples):
        raise ValueError("training requires labelled examples")
    w1 = 0.0
    w2 = 0.0
    for _ in range(100):
        g1 = g2 = 0.0
        for row in examples:
            margin = max(-40.0, min(40.0, row.label * (w1 * row.stable + w2 * row.nuisance)))
            factor = -row.label / (1 + math.exp(margin))
            g1 += factor * row.stable
            g2 += factor * row.nuisance
        w1 -= 0.3 * (g1 / len(examples) + 0.01 * w1)
        if not robust:
            w2 -= 0.3 * (g2 / len(examples) + 0.01 * w2)
    return w1, w2


def score_classifier(weights: tuple[float, float], examples: tuple[Example, ...]) -> float:
    if not examples or any(x.label not in (-1, 1) for x in examples):
        raise ValueError("scoring requires labelled examples")
    return sum(
        (weights[0] * x.stable + weights[1] * x.nuisance >= 0) != (x.label == 1) for x in examples
    ) / len(examples)


@dataclass(frozen=True)
class Job:
    arrival: float
    duration: float
    deadline: float
    weight: float
    needs_accelerator: bool


@dataclass(frozen=True)
class ScheduleResult:
    jobs_completed: int
    mean_weighted_tardiness: float


def generate_jobs(seed: int, count: int) -> tuple[Job, ...]:
    if count <= 0:
        raise ValueError("count must be positive")
    rng = random.Random(seed)
    arrival = 0.0
    jobs: list[Job] = []
    for _ in range(count):
        arrival += rng.expovariate(1 / 0.53)
        jobs.append(
            Job(
                arrival,
                rng.uniform(0.3, 1.6),
                arrival + rng.uniform(1, 5),
                8 if rng.random() < 0.25 else 1,
                rng.random() < 0.45,
            )
        )
    return tuple(jobs)


def schedule(jobs: tuple[Job, ...], policy: Literal["edf", "weighted"]) -> ScheduleResult:
    """Two servers share one accelerator; jobs are nonpreemptive."""
    if policy not in ("edf", "weighted") or not jobs:
        raise ValueError("unsupported policy or empty queue")
    if any(
        not all(math.isfinite(v) for v in (j.arrival, j.duration, j.deadline, j.weight))
        or j.arrival < 0
        or j.duration <= 0
        or j.weight <= 0
        for j in jobs
    ):
        raise ValueError("invalid job")
    ordered = sorted(enumerate(jobs), key=lambda x: (x[1].arrival, x[0]))
    pending: list[tuple[int, Job]] = []
    running: list[tuple[float, int, Job]] = []
    now = 0.0
    cursor = 0
    total = 0.0
    completed = 0
    while cursor < len(ordered) or pending or running:
        while running and running[0][0] <= now:
            finish, _, job = heapq.heappop(running)
            total += job.weight * max(0.0, finish - job.deadline)
            completed += 1
        while cursor < len(ordered) and ordered[cursor][1].arrival <= now:
            pending.append(ordered[cursor])
            cursor += 1
        while len(running) < 2:
            accelerator_busy = any(j.needs_accelerator for _, _, j in running)
            eligible = [
                (i, item)
                for i, item in enumerate(pending)
                if not (accelerator_busy and item[1].needs_accelerator)
            ]
            if not eligible:
                break
            if policy == "edf":
                idx, (identity, job) = min(eligible, key=lambda x: (x[1][1].deadline, x[1][0]))
            else:
                idx, (identity, job) = min(
                    eligible, key=lambda x: (x[1][1].duration / x[1][1].weight, x[1][0])
                )
            pending.pop(idx)
            heapq.heappush(running, (now + job.duration, identity, job))
        future: list[float] = []
        if cursor < len(ordered):
            future.append(ordered[cursor][1].arrival)
        if running:
            future.append(running[0][0])
        if future:
            now = min(future)
    return ScheduleResult(completed, total / len(jobs))
