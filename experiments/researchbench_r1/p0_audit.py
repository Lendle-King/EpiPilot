"""Zero-model P0 task-difficulty calibration and fail-closed readiness report.

This intentionally never returns P0 PASS: private scorer isolation, independent
gold references, and the P1 cost-capped model runner are not supplied by this
public diagnostic. Do not promote a public calibration score to hidden test.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, stdev
from typing import Any

from experiments.researchbench_r1.p0_tasks import (
    classifier_predictions,
    classifier_score,
    generate_queue,
    generate_shift,
    logistic_baseline,
    queue_baseline,
    score_queue,
)

SCHEDULING_REGIMES = ("tight", "contention", "weighted")
CLASSIFICATION_REGIMES = ("spurious", "interaction", "mixed")


def _std(values: list[float]) -> float:
    return stdev(values) if len(values) > 1 else 0.0


def calibrate(cases_per_regime: int = 4) -> dict[str, Any]:
    """Compute descriptive public controls from independent seeds within each regime."""
    if not 1 <= cases_per_regime <= 30:
        raise ValueError("cases_per_regime must be in [1, 30]")
    scheduling: dict[str, Any] = {}
    classification: dict[str, Any] = {}
    for family_index, regime in enumerate(SCHEDULING_REGIMES):
        records: list[dict[str, float]] = []
        for iteration in range(cases_per_regime):
            jobs = generate_queue(1000 * family_index + iteration + 90000, regime)
            scores = {
                name: score_queue(jobs, queue_baseline(jobs, name))
                for name in ("edf", "wspt", "hybrid")
            }
            records.append(scores)
        scheduling[regime] = {
            "baseline_hybrid_loss": mean(r["hybrid"] for r in records),
            "baseline_sd": _std([r["hybrid"] for r in records]),
            "edf_loss": mean(r["edf"] for r in records),
            "wspt_loss": mean(r["wspt"] for r in records),
            "heuristic_wins": {
                name: sum(r[name] <= min(r.values()) + 1e-9 for r in records)
                for name in ("edf", "wspt", "hybrid")
            },
            "case_count": cases_per_regime,
        }
    for family_index, regime in enumerate(CLASSIFICATION_REGIMES):
        records = []
        for iteration in range(cases_per_regime):
            seed = 1000 * family_index + iteration + 80000
            train = generate_shift(seed, regime, "train", 250)
            dev = generate_shift(seed + 10000, regime, "dev", 300)
            shift = generate_shift(seed + 20000, regime, "shift", 300)
            baseline = logistic_baseline(train)
            baseline_dev = classifier_score(dev, classifier_predictions(baseline, dev))
            baseline_shift = classifier_score(shift, classifier_predictions(baseline, shift))
            # A three-try cheap, fixed-seed control; selected ONLY using development data.
            factors = (0.0, 0.5, 1.0)
            candidates = []
            for factor in factors:
                modified = (*baseline[:3], baseline[3] * factor, *baseline[4:])
                dev_score = classifier_score(dev, classifier_predictions(modified, dev))
                candidates.append((dev_score["worst_group_error"], modified))
            selected = min(candidates, key=lambda row: row[0])[1]
            selected_shift = classifier_score(shift, classifier_predictions(selected, shift))
            records.append(
                {
                    "baseline_dev_error": baseline_dev["error"],
                    "baseline_shift_error": baseline_shift["error"],
                    "baseline_worst_group_error": baseline_shift["worst_group_error"],
                    "three_try_shift_error": selected_shift["error"],
                    "three_try_worst_group_error": selected_shift["worst_group_error"],
                }
            )
        classification[regime] = {name: mean(row[name] for row in records) for name in records[0]}
        classification[regime]["case_count"] = cases_per_regime

    return {
        "schema_version": 1,
        "status": "P0_BLOCKED",
        "study": "R1 P0 public calibration and safety gate",
        "model_calls": 0,
        "paid_experiments": 0,
        "scheduling": scheduling,
        "classification": classification,
        "preflight": {
            "public_deterministic_calibration": True,
            "private_test_isolation": False,
            "private_evaluator_adversarial_audit": False,
            "independent_strong_references": False,
            "p1_model_budget_and_receipts": False,
        },
        "blocking_reasons": [
            "Public candidate/reference task generator is development-only, not an agent task.",
            "Independent baseline and reference calibration has not been attested.",
            "Separate private evaluator job and adversarial isolation are not attested.",
            "P1 model usage, retries and budget enforcement are not implemented.",
        ],
        "qualification": (
            "No Agent-Only/EpiPilot comparison, private test, calibrated scientific "
            "discovery claim or proof of nontrivial task difficulty. "
            "P1 must remain disabled until independent security and task review."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline R1 P0 calibration (never runs P1)")
    parser.add_argument("--cases-per-regime", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = calibrate(args.cases_per_regime)
    text = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
    if args.output is not None:
        if args.output.exists():
            raise ValueError("calibration output is immutable; use a fresh path")
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 2  # Deliberately fail-closed: this diagnostic is not P0 acceptance.


if __name__ == "__main__":
    raise SystemExit(main())
