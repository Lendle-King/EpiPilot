"""Offline tests for Benchmark-First contracts, pinned sources and scored receipts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from epipilot.benchfirst.cli import main
from epipilot.benchfirst.contracts import BenchmarkPlan, normalized_quality, read_plan
from epipilot.benchfirst.evaluation import compare_receipts, progress_area
from epipilot.benchfirst.preflight import source_preflight

EXAMPLE = (
    Path(__file__).resolve().parents[1] / "experiments" / "benchmark_first" / "s0_example.json"
)


def example() -> dict[str, object]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _set_for_s1(raw: dict[str, object]) -> BenchmarkPlan:
    raw["stage"] = "s1"
    raw["evaluation_independence"] = "private_audited"
    raw["budget"] = {
        "max_model_attempts": 4,
        "max_dev_experiments": 3,
        "max_wall_seconds": 100,
    }
    return BenchmarkPlan.model_validate(raw)


def _make_ledger(tmp_path: Path, plan: BenchmarkPlan) -> Path:
    records = []
    for adapter in plan.adapters:
        for task in plan.tasks:
            for repeat in range(plan.replicates):
                records.append(
                    {
                        "adapter_id": adapter.id,
                        "task_id": task.id,
                        "replicate": repeat,
                        "status": "success",
                        "model_attempts": 2,
                        "dev_experiments": 1,
                        "elapsed_seconds": 60,
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cached_input_tokens": None,
                        "billed_usd": None,
                        "evaluator_sha256": task.evaluator_sha256,
                        "sealed_scoring": True,
                        "checkpoints": [
                            {
                                "elapsed_seconds": 30,
                                "score": 0.5,
                                "candidate_sha256": "a" * 64,
                                "score_artifact_sha256": "b" * 64,
                            }
                        ],
                        "log_sha256": "c" * 64,
                    }
                )
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return ledger


def test_example_is_explicitly_s0_and_non_executable() -> None:
    plan = read_plan(EXAMPLE)
    assert plan.stage == "s0"
    assert plan.budget.max_model_attempts == 0
    assert plan.approval_status == "not_authorized"
    assert plan.evaluation_independence == "not_audited"
    assert len(plan.adapters) == 5
    assert all(not adapter.adapter_qualified for adapter in plan.adapters)


def test_manifest_requires_model_parity_in_matched_track() -> None:
    raw = example()
    raw["adapters"][0]["model"] = {
        "provider": "different",
        "model_id": "stronger",
        "reasoning_effort": "max",
        "transport": "other",
    }
    with pytest.raises(ValidationError, match="cannot mix models"):
        BenchmarkPlan.model_validate(raw)


def test_manifest_rejects_duplicate_arm_and_unapproved_s0() -> None:
    raw = example()
    raw["adapters"][1]["id"] = raw["adapters"][0]["id"]
    with pytest.raises(ValidationError, match="duplicate adapter"):
        BenchmarkPlan.model_validate(raw)
    raw = example()
    raw["budget"]["max_model_attempts"] = 1
    with pytest.raises(ValidationError, match="offline-only"):
        BenchmarkPlan.model_validate(raw)


def test_manifest_nonzero_gap_and_path_traversal() -> None:
    raw = example()
    raw["tasks"][0]["reference"] = 0.0
    with pytest.raises(ValidationError, match="zero"):
        BenchmarkPlan.model_validate(raw)
    raw = example()
    raw["adapters"][0]["checkout"] = "../secret"
    with pytest.raises(ValidationError):
        BenchmarkPlan.model_validate(raw)


def test_normalized_quality_and_anytime_area() -> None:
    plan = read_plan(EXAMPLE)
    task = plan.tasks[0]
    assert normalized_quality(task, 0.5) == pytest.approx(0.5)
    assert normalized_quality(task, 1.5) == 1
    assert progress_area([(20, 0.5), (50, 0.3), (75, 1)], 100) == pytest.approx(0.525)


def test_complete_matched_receipts_are_descriptive_not_superiority(
    tmp_path: Path,
) -> None:
    plan = _set_for_s1(example())
    ledger = _make_ledger(tmp_path, plan)
    report = compare_receipts(plan, ledger)
    assert report["cells"] == 10
    assert report["status"] == "descriptive_only"
    assert report["provider_billing_complete"] is False
    assert report["per_adapter"]["aide_ml"]["mean_progress_area"] == pytest.approx(0.35)


def test_missing_cell_and_budget_overrun_are_rejected(tmp_path: Path) -> None:
    plan = _set_for_s1(example())
    ledger = _make_ledger(tmp_path, plan)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    ledger.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        compare_receipts(plan, ledger)
    row = json.loads(lines[0])
    row["model_attempts"] = 5
    ledger.write_text("\n".join([json.dumps(row), *lines[1:]]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="budget"):
        compare_receipts(plan, ledger)


def test_unverified_checkpoint_cannot_count(tmp_path: Path) -> None:
    plan = _set_for_s1(example())
    ledger = _make_ledger(tmp_path, plan)
    rows = [json.loads(x) for x in ledger.read_text().splitlines()]
    rows[0]["sealed_scoring"] = False
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="unverified checkpoint"):
        compare_receipts(plan, ledger)


def test_cli_catalog_validate_and_source_preflight(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["catalog"]) == 0
    assert "aira_dojo" in capsys.readouterr().out
    assert main(["validate", "--manifest", str(EXAMPLE)]) == 0
    assert "schema_valid_only" in capsys.readouterr().out
    plan = read_plan(EXAMPLE)
    status = source_preflight(plan, tmp_path)
    assert status["source_pins_match"] is False
    assert status["model_calls"] == 0


def test_valid_local_clean_git_checkout_is_inspected(tmp_path: Path) -> None:
    checkout = tmp_path / "checkouts" / "EpiPilot"
    checkout.mkdir(parents=True)
    for args in (
        ("init", "-q"),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "CI test"),
    ):
        subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True)
    (checkout / "README.md").write_text("local mock source\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=checkout, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "test"], cwd=checkout, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout).decode().strip()
    raw = example()
    raw["adapters"] = raw["adapters"][:1] + raw["adapters"][-1:]
    for adapter in raw["adapters"]:
        adapter["source_sha"] = sha
    report = source_preflight(BenchmarkPlan.model_validate(raw), tmp_path)
    assert report["source_pins_match"] is True
    assert report["adapter_self_reported_qualification"] is False
