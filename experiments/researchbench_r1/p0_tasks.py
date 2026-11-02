"""P0-only reference-free task interfaces and calibration populations.

The generation code is for BENCHMARK DEVELOPMENT, not an agent task bundle.
Formal runs must expose frozen training/dev inputs and a separately reviewed
candidate interface, never private test labels or evaluation seeds.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal

QueueRegime = Literal["tight", "contention", "weighted"]
ShiftRegime = Literal["spurious", "interaction", "mixed"]


@dataclass(frozen=True)
class Job:
    id: str
    release: float
    duration: float
    deadline: float
    weight: float
    accelerator: bool


@dataclass(frozen=True)
class Placement:
    job_id: str
    server: int
    start: float


@dataclass(frozen=True)
class Observation:
    id: str
    stable_a: float
    stable_b: float
    nuisance: float
    group: int
    label: int


def generate_queue(seed: int, regime: QueueRegime, count: int = 80) -> tuple[Job, ...]:
    """Two servers, one shared accelerator, variable deadline/weight regimes."""
    if regime not in ("tight", "contention", "weighted") or not 10 <= count <= 500:
        raise ValueError("invalid scheduling population")
    rng = random.Random(seed)
    clock = 0.0
    jobs: list[Job] = []
    for index in range(count):
        clock += rng.expovariate(1.0 / (0.38 if regime == "tight" else 0.57))
        duration = rng.uniform(0.35, 2.0)
        slack = rng.uniform(0.15, 1.3) if regime == "tight" else rng.uniform(1.0, 4.3)
        weight = (
            rng.choice((1.0, 2.0, 9.0))
            if regime == "weighted"
            else (7.0 if rng.random() < 0.2 else 1.0)
        )
        jobs.append(
            Job(
                id=f"J{index:04d}",
                release=round(clock, 7),
                duration=round(duration, 7),
                deadline=round(clock + duration + slack, 7),
                weight=weight,
                accelerator=rng.random() < (0.77 if regime == "contention" else 0.3),
            )
        )
    return tuple(jobs)


def score_queue(jobs: tuple[Job, ...], plan: tuple[Placement, ...]) -> float:
    """Independently recompute nonpreemptive, accelerator-constrained tardiness."""
    if not jobs or len(jobs) != len(plan):
        raise ValueError("every job must appear exactly once")
    indexed = {job.id: job for job in jobs}
    if len(indexed) != len(jobs) or {item.job_id for item in plan} != set(indexed):
        raise ValueError("duplicate or missing job identities")
    servers: dict[int, list[tuple[float, float]]] = {0: [], 1: []}
    accelerator: list[tuple[float, float]] = []
    weighted = 0.0
    for item in plan:
        if item.server not in servers or not math.isfinite(item.start):
            raise ValueError("invalid server or start time")
        job = indexed[item.job_id]
        if not (
            math.isfinite(job.release)
            and math.isfinite(job.duration)
            and math.isfinite(job.deadline)
            and math.isfinite(job.weight)
        ):
            raise ValueError("nonfinite job")
        if job.duration <= 0 or job.weight <= 0 or item.start + 1e-8 < job.release:
            raise ValueError("invalid duration, weight or early start")
        finish = item.start + job.duration
        for first, last in servers[item.server]:
            if item.start < last - 1e-8 and finish > first + 1e-8:
                raise ValueError("overlapping server usage")
        servers[item.server].append((item.start, finish))
        if job.accelerator:
            for first, last in accelerator:
                if item.start < last - 1e-8 and finish > first + 1e-8:
                    raise ValueError("overlapping accelerator usage")
            accelerator.append((item.start, finish))
        weighted += job.weight * max(0.0, finish - job.deadline)
    return weighted / len(jobs)


def queue_baseline(
    jobs: tuple[Job, ...], strategy: Literal["hybrid", "edf", "wspt"] = "hybrid"
) -> tuple[Placement, ...]:
    """Auditable deterministic heuristic; not claimed optimal on any regime."""
    if strategy not in ("hybrid", "edf", "wspt"):
        raise ValueError("unknown scheduling strategy")

    def priority(job: Job) -> tuple[float, str]:
        if strategy == "edf":
            return job.deadline, job.id
        if strategy == "wspt":
            return job.duration / job.weight, job.id
        slack = max(0.1, job.deadline - job.release - job.duration)
        return job.duration / job.weight + 0.2 * slack, job.id

    available = [0.0, 0.0]
    accelerator_free = 0.0
    plan: list[Placement] = []
    for job in sorted(jobs, key=priority):
        candidates = [
            (max(job.release, free, accelerator_free if job.accelerator else 0.0), server)
            for server, free in enumerate(available)
        ]
        start, server = min(candidates)
        finish = start + job.duration
        available[server] = finish
        if job.accelerator:
            accelerator_free = finish
        plan.append(Placement(job.id, server, start))
    return tuple(plan)


def generate_shift(
    seed: int, regime: ShiftRegime, split: Literal["train", "dev", "shift"], count: int
) -> tuple[Observation, ...]:
    """Public calibration generator; exclude its code and parameterization from agent bundle."""
    if (
        regime not in ("spurious", "interaction", "mixed")
        or split not in ("train", "dev", "shift")
        or not 40 <= count <= 5000
    ):
        raise ValueError("invalid classification population")
    rng = random.Random(seed)
    samples: list[Observation] = []
    rho = {
        "train": (1.0, 0.7),
        "dev": (0.05, -0.45),
        "shift": (-0.6, 0.35),
    }[split]
    for index in range(count):
        a, b = rng.gauss(0, 1), rng.gauss(0, 1)
        group = int(rng.random() < (0.3 if split == "train" else 0.5))
        interaction = 0.8 * a * b if regime != "spurious" else 0.0
        effect = (0.3 if group else -0.2) * a if regime != "spurious" else 0.0
        margin = 1.05 * a + 0.65 * b + interaction + effect
        label = int(margin + rng.gauss(0, 0.65) >= 0)
        corr = rho[group] * (0.7 if regime == "interaction" else 1.0)
        nuisance = (2 * label - 1) * corr + rng.gauss(0, 0.65)
        samples.append(Observation(f"S{index:05d}", a, b, nuisance, group, label))
    return tuple(samples)


def logistic_baseline(
    train: tuple[Observation, ...], *, regularization: float = 0.08
) -> tuple[float, ...]:
    """A trainable six-feature regularized logistic reference baseline."""
    if not train or regularization < 0 or not math.isfinite(regularization):
        raise ValueError("invalid training rows or regularization")
    weights = [0.0] * 6
    for _ in range(125):
        grad = [0.0] * len(weights)
        for row in train:
            features = (
                1.0,
                row.stable_a,
                row.stable_b,
                row.nuisance,
                row.stable_a * row.stable_b,
                row.group * row.stable_a,
            )
            z = max(-30.0, min(30.0, sum(w * x for w, x in zip(weights, features, strict=True))))
            p = 1.0 / (1.0 + math.exp(-z))
            for index, x in enumerate(features):
                grad[index] += (p - row.label) * x
        weights = [
            w - 0.23 * (g / len(train) + (0.0 if i == 0 else regularization * w))
            for i, (w, g) in enumerate(zip(weights, grad, strict=True))
        ]
    return tuple(weights)


def classifier_predictions(
    weights: tuple[float, ...], rows: tuple[Observation, ...]
) -> dict[str, int]:
    if len(weights) != 6 or any(not math.isfinite(w) for w in weights):
        raise ValueError("invalid classifier")
    result: dict[str, int] = {}
    for row in rows:
        features = (
            1.0,
            row.stable_a,
            row.stable_b,
            row.nuisance,
            row.stable_a * row.stable_b,
            row.group * row.stable_a,
        )
        result[row.id] = int(sum(w * x for w, x in zip(weights, features, strict=True)) >= 0)
    return result


def classifier_score(
    rows: tuple[Observation, ...], predictions: dict[str, int]
) -> dict[str, float]:
    if not rows or len({row.id for row in rows}) != len(rows):
        raise ValueError("invalid evaluation rows")
    if set(predictions) != {row.id for row in rows} or any(
        type(value) is not int or value not in (0, 1) for value in predictions.values()
    ):
        raise ValueError("missing, extra or invalid predictions")
    errors = [float(predictions[row.id] != row.label) for row in rows]
    by_group = {
        group: [float(predictions[row.id] != row.label) for row in rows if row.group == group]
        for group in (0, 1)
    }
    if not all(by_group.values()):
        raise ValueError("both groups must be represented")
    return {
        "error": sum(errors) / len(errors),
        "worst_group_error": max(sum(values) / len(values) for values in by_group.values()),
    }
