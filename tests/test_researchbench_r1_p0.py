"""P0 reference-free difficulty, prediction-only scoring and boundary regressions."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from experiments.researchbench_r1.p0_audit import calibrate
from experiments.researchbench_r1.p0_tasks import (
    Placement,
    classifier_predictions,
    classifier_score,
    generate_queue,
    generate_shift,
    logistic_baseline,
    queue_baseline,
    score_queue,
)
from experiments.researchbench_r1.sealed_eval import (
    private_score_file,
    score_private_classification,
    score_private_queue,
)


def test_queue_regimes_reproducible_and_legal() -> None:
    for regime in ("tight", "contention", "weighted"):
        jobs = generate_queue(22, regime, count=60)
        assert jobs == generate_queue(22, regime, count=60)
        assert jobs != generate_queue(23, regime, count=60)
        assert {job.id for job in jobs} == {f"J{i:04d}" for i in range(60)}
        assert any(job.accelerator for job in jobs)
        for strategy in ("hybrid", "edf", "wspt"):
            plan = queue_baseline(jobs, strategy)
            assert len(plan) == len(jobs)
            assert score_queue(jobs, plan) >= 0


def test_queue_catches_resource_conflicts_and_missing_jobs() -> None:
    jobs = generate_queue(17, "contention", count=40)
    baseline = queue_baseline(jobs)
    with pytest.raises(ValueError, match="every job"):
        score_queue(jobs, baseline[:-1])
    duplicated = (*baseline[:-1], baseline[0])
    with pytest.raises(ValueError, match="duplicate or missing"):
        score_queue(jobs, duplicated)
    early = (Placement(baseline[0].job_id, 0, -1.0), *baseline[1:])
    with pytest.raises(ValueError, match="early start"):
        score_queue(jobs, early)
    wrong_server = (Placement(baseline[0].job_id, 7, baseline[0].start), *baseline[1:])
    with pytest.raises(ValueError, match="invalid server"):
        score_queue(jobs, wrong_server)
    assert jobs[0].duration > 0


def test_shift_regimes_trainable_and_group_scored() -> None:
    for regime in ("spurious", "interaction", "mixed"):
        train = generate_shift(12, regime, "train", 200)
        dev = generate_shift(31, regime, "dev", 160)
        shifted = generate_shift(71, regime, "shift", 160)
        assert train == generate_shift(12, regime, "train", 200)
        assert shifted != dev
        weights = logistic_baseline(train)
        predictions = classifier_predictions(weights, shifted)
        scores = classifier_score(shifted, predictions)
        assert 0 <= scores["error"] <= scores["worst_group_error"] <= 1
        with pytest.raises(ValueError, match="missing, extra"):
            classifier_score(shifted, {})
        with pytest.raises(ValueError, match="missing, extra"):
            classifier_score(shifted, {**predictions, shifted[0].id: True})


def _private_fixture(tmp_path: Path) -> tuple[Path, Path]:
    data = generate_shift(99, "interaction", "shift", 80)
    weights = logistic_baseline(generate_shift(23, "interaction", "train", 100))
    predictions = classifier_predictions(weights, data)
    submit = {
        "schema_version": 1,
        "task_id": "frozen-01",
        "kind": "classification",
        "candidate_sha256": "a" * 64,
        "rows": [{"sample_id": item.id, "prediction": predictions[item.id]} for item in data],
    }
    private = {
        "schema_version": 1,
        "task_id": "frozen-01",
        "kind": "classification",
        "rows": [asdict(row) for row in data],
    }
    submission_path = tmp_path / "submission.json"
    private_path = tmp_path / "private.json"
    submission_path.write_text(json.dumps(submit), encoding="utf-8")
    private_path.write_text(json.dumps(private), encoding="utf-8")
    return submission_path, private_path


def test_private_scorer_uses_predictions_only_and_aggregates(tmp_path: Path) -> None:
    submission, private = _private_fixture(tmp_path)
    result = private_score_file(submission, private, task_id="frozen-01", kind="classification")
    assert result["candidate_sha256"] == "a" * 64
    assert len(result["submission_sha256"]) == 64
    assert result["scores"]["worst_group_error"] >= result["scores"]["error"]
    assert "private" not in result and "rows" not in result
    assert "label" not in json.dumps(result)


@pytest.mark.parametrize("mutation", ["bad_task", "duplicates", "nan", "missing", "bool"])
def test_private_scorer_rejects_invalid_submission(tmp_path: Path, mutation: str) -> None:
    submission, private = _private_fixture(tmp_path)
    contents = json.loads(submission.read_text())
    if mutation == "bad_task":
        contents["task_id"] = "other"
    elif mutation == "duplicates":
        contents["rows"].append(contents["rows"][0])
    elif mutation == "nan":
        contents["rows"][0]["prediction"] = float("nan")
    elif mutation == "missing":
        contents["rows"].pop()
    else:
        contents["rows"][0]["prediction"] = True
    submission.write_text(json.dumps(contents), encoding="utf-8")
    with pytest.raises(ValueError):
        private_score_file(submission, private, task_id="frozen-01", kind="classification")


def test_private_scorer_rejects_duplicate_keys_and_oversized_file(tmp_path: Path) -> None:
    submission, private = _private_fixture(tmp_path)
    submission.write_text('{"task_id":"frozen-01","task_id":"other"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        private_score_file(submission, private, task_id="frozen-01", kind="classification")


def test_private_queue_scorer_never_evaluates_candidate_source() -> None:
    jobs = generate_queue(5, "tight", count=20)
    plan = queue_baseline(jobs)
    submit = {
        "schema_version": 1,
        "task_id": "sched-01",
        "kind": "scheduling",
        "candidate_sha256": "b" * 64,
        "rows": [asdict(item) for item in plan],
    }
    report = score_private_queue(submit, task_id="sched-01", private_jobs=jobs)
    assert report["scores"]["mean_weighted_tardiness"] == pytest.approx(score_queue(jobs, plan))
    submit["rows"][0]["server"] = 9
    with pytest.raises(ValueError):
        score_private_queue(submit, task_id="sched-01", private_jobs=jobs)


def test_private_classification_rejects_leaked_label_field() -> None:
    samples = generate_shift(8, "spurious", "dev", 50)
    submit = {
        "schema_version": 1,
        "task_id": "frozen-02",
        "kind": "classification",
        "candidate_sha256": "c" * 64,
        "rows": [{"sample_id": sample.id, "prediction": 0} for sample in samples],
    }
    submit["rows"][0]["label"] = 1
    with pytest.raises(ValueError, match="unexpected or missing"):
        score_private_classification(submit, task_id="frozen-02", private_rows=samples)


def test_public_p0_stays_blocked_even_when_baselines_execute() -> None:
    report = calibrate(1)
    assert report["status"] == "P0_BLOCKED"
    assert report["model_calls"] == 0
    assert report["preflight"]["private_test_isolation"] is False
    assert report["preflight"]["independent_strong_references"] is False
    assert set(report["scheduling"]) == {"tight", "contention", "weighted"}
    assert set(report["classification"]) == {"spurious", "interaction", "mixed"}


def test_same_user_lock_does_not_depend_on_runner_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from experiments.researchbench_r1.resource_guard import (
        ResourceRefusal,
        single_runner_lock,
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner-one"))
    with single_runner_lock():
        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner-two"))
        with pytest.raises(ResourceRefusal, match="already owns"), single_runner_lock():
            pass


@pytest.mark.parametrize("strategy", ["edf", "wspt", "hybrid"])
def test_queue_baseline_does_not_delay_available_jobs_for_future_jobs(strategy: str) -> None:
    from experiments.researchbench_r1.p0_tasks import Job

    jobs = (
        Job("available-a", 0.0, 2.0, 10.0, 1.0, False),
        Job("available-b", 0.0, 2.0, 10.0, 1.0, False),
        Job("future-high-priority", 100.0, 0.5, 101.0, 1000.0, False),
    )
    placements = {p.job_id: p for p in queue_baseline(jobs, strategy)}
    assert placements["available-a"].start == 0
    assert placements["available-b"].start == 0
    assert score_queue(jobs, tuple(placements.values())) == 0
