"""Deterministic decision-table arithmetic; no scientific truth or I/O."""

from __future__ import annotations

from epipilot.research.intelligence_models import (
    BranchKind,
    CalibrationSample,
    DecisionEstimate,
)


def score_estimate(
    estimate: DecisionEstimate,
    probabilities: tuple[float, ...] | None = None,
    enabled_before: tuple[bool, ...] | None = None,
    enabled_after: tuple[tuple[bool, ...], ...] | None = None,
) -> float:
    """Value of adapting the next decision; unlocking is counted here exactly once.

    Recompute the prior from the same joint table after probability correction. A
    change in belief alone must not masquerade as a benefit of switching choices.
    """
    p = probabilities or tuple(b.probability for b in estimate.branches)
    before = enabled_before or (True,) * len(estimate.options)
    after = enabled_after or (before,) * len(p)
    if (
        len(p) != len(estimate.branches)
        or len(before) != len(estimate.options)
        or len(after) != len(p)
        or not any(before)
    ):
        raise ValueError("decision mask dimensions or baseline invalid")
    baseline = max(
        sum(w * b.utilities[i] for w, b in zip(p, estimate.branches, strict=True))
        for i, available in enumerate(before)
        if available
    )
    posterior = 0.0
    for weight, branch, mask in zip(p, estimate.branches, after, strict=True):
        if len(mask) != len(before) or not any(mask):
            raise ValueError("each branch requires a feasible stop/defer option")
        posterior += weight * max(
            u for u, allowed in zip(branch.utilities, mask, strict=True) if allowed
        )
    return posterior - baseline


def calibrate_probabilities(
    estimate: DecisionEstimate,
    samples: tuple[CalibrationSample, ...],
    *,
    group: str,
    minimum_samples: int = 20,
    prior_strength: float = 20.0,
) -> tuple[float, ...]:
    prior = tuple(b.probability for b in estimate.branches)
    compatible = [
        s
        for s in samples
        if s.predictor == estimate.predictor
        and s.utility_version == estimate.utility_version
        and s.group == group
    ]
    if len(compatible) < minimum_samples:
        return prior
    adjusted = tuple(
        max(
            1e-9,
            p
            + sum(float(s.outcome is kind) - s.probabilities[i] for s in compatible)
            / (prior_strength + len(compatible)),
        )
        for i, (p, kind) in enumerate(zip(prior, BranchKind, strict=True))
    )
    return tuple(v / sum(adjusted) for v in adjusted)
