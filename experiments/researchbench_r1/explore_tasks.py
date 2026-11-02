"""Exploratory public calibration; output is not a private evaluation."""

from __future__ import annotations

import json
import random
from statistics import mean, stdev

from experiments.researchbench_r1.tasks import (
    fit_classifier,
    generate_examples,
    generate_jobs,
    schedule,
    score_classifier,
)


def main() -> None:
    scheduling: list[tuple[float, float]] = []
    classification: list[tuple[float, float, float]] = []
    for seed in range(30):
        jobs = generate_jobs(10000 + seed, 150)
        scheduling.append(
            (
                schedule(jobs, "edf").mean_weighted_tardiness,
                schedule(jobs, "weighted").mean_weighted_tardiness,
            )
        )
        train = generate_examples(20000 + seed, 500, 0.9)
        dev = generate_examples(30000 + seed, 500, -0.3)
        calibration = generate_examples(40000 + seed, 500, -0.3)
        base = fit_classifier(train, robust=False)
        reference = fit_classifier(train, robust=True)
        rng = random.Random(50000 + seed)
        candidates = [base, *((base[0], base[1] * rng.uniform(0, 1.5)) for _ in range(3))]
        selected = min(candidates, key=lambda x: score_classifier(x, dev))
        classification.append(
            (
                score_classifier(base, calibration),
                score_classifier(reference, calibration),
                score_classifier(selected, calibration),
            )
        )
    s_delta = [a - b for a, b in scheduling]
    c_delta = [a - b for a, b, _ in classification]
    result = {
        "status": "exploratory_public_not_p0_pass",
        "cases": 30,
        "scheduling": {
            "baseline_mean": mean(a for a, _ in scheduling),
            "reference_mean": mean(b for _, b in scheduling),
            "paired_improvement_mean": mean(s_delta),
            "paired_improvement_sd": stdev(s_delta),
            "reference_wins": sum(x > 0 for x in s_delta),
        },
        "classification": {
            "baseline_error": mean(a for a, _, _ in classification),
            "reference_error": mean(b for _, b, _ in classification),
            "random_search_error": mean(c for _, _, c in classification),
            "paired_improvement_mean": mean(c_delta),
            "paired_improvement_sd": stdev(c_delta),
            "reference_wins": sum(x > 0 for x in c_delta),
            "random_at_least_half_reference_gain": sum(
                a - c >= 0.5 * (a - b) for a, b, c in classification
            ),
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
