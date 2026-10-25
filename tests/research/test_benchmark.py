"""Synthetic fixtures test paired benchmark validity, not research improvements."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from epipilot.research.benchmark import InvalidBenchmark, compare_suite
from epipilot.research.cli import main


def fixture(tmp_path: Path) -> Path:
    cases = [{"case_id": "a", "seed": 1}, {"case_id": "b", "seed": 2}]
    runs = []
    for policy, values, costs in (
        ("agent_only", [0.2, 0.5], [8, 9]),
        ("epipilot_heuristic", [0.4, 0.7], [6, 7]),
    ):
        for case, score, cost in zip(cases, values, costs, strict=True):
            rel = f"{policy}-{case['case_id']}.json"
            artifact = tmp_path / rel
            artifact.write_text(
                json.dumps(
                    {
                        "case_id": case["case_id"],
                        "seed": case["seed"],
                        "dataset": "frozen-suite-v1",
                        "evaluator": "checker-v1",
                        "metric": "quality",
                        "value": score,
                    }
                ),
                encoding="utf-8",
            )
            runs.append(
                {
                    "policy": policy,
                    "case_id": case["case_id"],
                    "seed": case["seed"],
                    "artifact_ref": rel,
                    "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    "actual_cost": cost,
                    "human_interventions": 0,
                    "repeated_experiments": 0,
                    "unsupported_claims": 0,
                }
            )
    data = {
        "schema_version": 1,
        "name": "synthetic-smoke",
        "dataset": "frozen-suite-v1",
        "evaluator": "checker-v1",
        "metric": "quality",
        "direction": "maximize",
        "budget_unit": "shared-cost-units",
        "per_case_budget": 10,
        "baseline": "agent_only",
        "policies": ["agent_only", "epipilot_heuristic"],
        "cases": cases,
        "runs": runs,
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return manifest


def modify(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if field == "remove":
        data["runs"].pop()
    elif field == "duplicate":
        data["runs"].append(dict(data["runs"][-1]))
    elif field == "budget":
        data["runs"][0]["actual_cost"] = value
    elif field == "direction":
        data["direction"] = value
    path.write_text(json.dumps(data), encoding="utf-8")


def test_policy_comparison_is_matched(tmp_path: Path) -> None:
    path = fixture(tmp_path)
    results = compare_suite(path)
    assert results["matched_cases"] == 2
    assert results["policies"]["epipilot_heuristic"]["mean_paired_improvement"] == pytest.approx(0.2)
    assert results["policies"]["epipilot_heuristic"]["mean_cost"] == 6.5
    assert results["policies"]["epipilot_heuristic"]["paired_wins"] == 2
    assert main(["--root", str(tmp_path), "benchmark", "--manifest", "manifest.json"]) == 0
    modify(path, "direction", "minimize")
    assert compare_suite(path)["policies"]["epipilot_heuristic"][
        "mean_paired_improvement"
    ] == pytest.approx(-0.2)


@pytest.mark.parametrize("fault", ["remove", "duplicate", "budget"])
def test_missing_duplicate_or_overbudget_case_is_rejected(tmp_path: Path, fault: str) -> None:
    path = fixture(tmp_path)
    modify(path, fault, 12)
    with pytest.raises((ValueError, ValidationError, InvalidBenchmark)):
        compare_suite(path)
    assert main(["--root", str(tmp_path), "benchmark", "--manifest", "manifest.json"]) == 2


@pytest.mark.parametrize("fault", ["tamper", "wrong_dataset", "invalid_type"])
def test_metric_artifact_mismatch_fails_closed(tmp_path: Path, fault: str) -> None:
    manifest = fixture(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    row = data["runs"][0]
    path = tmp_path / row["artifact_ref"]
    item = json.loads(path.read_text(encoding="utf-8"))
    if fault == "tamper":
        item["value"] = 0.99
    elif fault == "wrong_dataset":
        item["dataset"] = "training"
    else:
        item["value"] = True
    path.write_text(json.dumps(item), encoding="utf-8")
    if fault != "tamper":
        row["artifact_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InvalidBenchmark):
        compare_suite(manifest)


def test_too_few_comparisons_are_rejected(tmp_path: Path) -> None:
    path = fixture(tmp_path)
    with pytest.raises(InvalidBenchmark, match="too few"):
        compare_suite(path, min_cases=3)
