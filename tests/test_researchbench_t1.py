"""T1 contract regressions; paid agents and dataset downloads are never run in CI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from experiments.researchbench_t1.t1 import (
    STAGES,
    BenchmarkError,
    add_memory,
    finalize,
    ledger,
    normalize_candidate,
    perform_trial,
    recover_failed_trial,
    select,
    setup,
    smoke,
    status,
    task_prompt,
)


def _candidate(name: str = "logistic") -> dict[str, object]:
    if name == "logistic":
        return {"model": "logistic", "feature_mode": "all", "C": 1.0, "class_weight": "none"}
    return {
        "model": "forest",
        "feature_mode": "all",
        "n_estimators": 64,
        "max_depth": 7,
        "min_samples_leaf": 2,
        "class_weight": "balanced",
    }


def _write(path: Path, content: dict[str, object]) -> Path:
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def test_default_stage_and_caps_are_small_and_offline() -> None:
    assert STAGES["smoke"]["arms"] == ["offline_baseline"]
    assert STAGES["smoke"]["total_seconds"] <= 30
    assert STAGES["pilot"]["per_cell_trials"] == 2
    assert STAGES["full"]["datasets"] == ["adult", "bank", "magic"]
    assert len(STAGES["full"]["arms"]) == 4
    assert len(STAGES["full"]["seeds"]) == 3
    assert STAGES["full"]["total_seconds"] <= 900


def test_candidate_allowlist_is_frozen_and_rejects_untrusted_execution() -> None:
    assert normalize_candidate(_candidate()) == dict(sorted(_candidate().items()))
    assert normalize_candidate(_candidate("forest"))["model"] == "forest"
    for rejected in (
        {"model": "exec", "feature_mode": "all", "command": "sh -c echo hack"},
        {**_candidate(), "C": 1000},
        {**_candidate(), "C": float("nan")},
        {**_candidate(), "payload": "import os"},
        {**_candidate("forest"), "n_estimators": 100000},
        {**_candidate("forest"), "max_depth": True},
        {**_candidate(), "feature_mode": "use_private_test"},
    ):
        with pytest.raises(BenchmarkError):
            normalize_candidate(rejected)


def test_agent_stages_require_explicit_opt_in(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkError, match="explicitly enabled"):
        setup(tmp_path / "public", tmp_path / "private", "pilot", {}, False)
    with pytest.raises(BenchmarkError, match="explicitly enabled"):
        setup(tmp_path / "public", tmp_path / "private", "full", {}, False)
    assert not (tmp_path / "public").exists()


def test_same_path_cannot_contain_private_holdout(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkError, match="disjoint"):
        setup(tmp_path / "one", tmp_path / "one" / "vault", "smoke", {}, False)


def test_full_stage_never_automatically_downloads_missing_data(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkError, match="supply an existing local raw UCI file"):
        setup(tmp_path / "public", tmp_path / "vault", "full", {}, True)


def test_cli_rejects_trials_without_initialized_workspace(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkError):
        perform_trial(tmp_path, "agent_only", "adult", 42, tmp_path / "missing.json")


def test_actual_cpu_smoke_chooses_dev_model_once(tmp_path: Path) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    work, vault = tmp_path / "work", tmp_path / "vault"
    first = smoke(work, vault)
    assert first["trials"][0]["exit_code"] == 0
    assert first["trials"][1]["exit_code"] == 0
    assert 0.5 < first["final"]["holdout_auroc"]["offline_baseline__wdbc__42"] <= 1
    assert first["status"]["completed"] == 2
    assert first["status"]["reserved_or_spent_seconds"] <= STAGES["smoke"]["total_seconds"]
    assert first["status"]["holdout_evaluated"]
    assert (work / "final.started").exists()
    with pytest.raises(BenchmarkError, match="already been evaluated"):
        finalize(work, vault)
    assert not (work / "public" / "wdbc" / "test.csv").exists()
    assert (vault / "wdbc" / "test.csv").exists()
    with pytest.raises(BenchmarkError, match="per-cell experiment count"):
        perform_trial(
            work,
            "offline_baseline",
            "wdbc",
            42,
            Path(__file__).parent.parent
            / "experiments"
            / "researchbench_t1"
            / "candidates"
            / "logistic.json",
        )


def test_trial_duplicate_and_data_integrity_guards(tmp_path: Path) -> None:
    pytest.importorskip("sklearn")
    work, vault = tmp_path / "work", tmp_path / "vault"
    setup(work, vault, "smoke", {}, False)
    candidate = _write(tmp_path / "candidate.json", _candidate())
    first = perform_trial(work, "offline_baseline", "wdbc", 42, candidate)
    assert first["exit_code"] == 0
    with pytest.raises(BenchmarkError, match="identical candidate"):
        perform_trial(work, "offline_baseline", "wdbc", 42, candidate)
    # Source result file is immutable: verified development output cannot be replaced.
    score_path = work / "runs" / first["id"] / "metric.json"
    score_path.write_text('{"auroc":0.01}', encoding="utf-8")
    with pytest.raises(BenchmarkError, match="changed since"):
        select(work, "offline_baseline", "wdbc", 42)
    # No accepted selected candidate or final benchmark result is produced.
    assert not (work / "final.started").exists()


def test_manual_crash_reconciliation_is_conservative(tmp_path: Path) -> None:
    pytest.importorskip("sklearn")
    work, vault = tmp_path / "work", tmp_path / "vault"
    setup(work, vault, "smoke", {}, False)
    events = work / "events.jsonl"
    begin = {
        "kind": "started",
        "id": "trial-0001",
        "arm": "offline_baseline",
        "dataset": "wdbc",
        "seed": 42,
        "candidate_sha256": "0" * 64,
        "time_limit": 12,
    }
    events.write_text(json.dumps(begin) + "\n", encoding="utf-8")
    candidate = _write(tmp_path / "new.json", _candidate())
    with pytest.raises(BenchmarkError, match="unreconciled"):
        perform_trial(work, "offline_baseline", "wdbc", 42, candidate)
    recovered = recover_failed_trial(work, "trial-0001", "operator checked missing worker")
    assert recovered["charged_seconds"] == 12
    with pytest.raises(BenchmarkError, match="only an uncompleted"):
        recover_failed_trial(work, "trial-0001", "second attempt to release reserve")
    assert len(ledger(work)) == 2
    assert status(work)["reserved_or_spent_seconds"] == 12


def test_policy_prompt_and_scoped_memory(tmp_path: Path) -> None:
    pytest.importorskip("sklearn")
    work, vault = tmp_path / "work", tmp_path / "vault"
    setup(work, vault, "pilot", {}, True)
    candidate = _write(tmp_path / "candidate.json", _candidate())
    result = perform_trial(work, "memory_only", "wdbc", 42, candidate)
    assert result["exit_code"] == 0
    note = "On the shared dev split, logistic regression performed well with all features."
    add_memory(work, "memory_only", "wdbc", 42, result["id"], note)
    message = task_prompt(work, "memory_only", "wdbc", 137)
    assert note in message
    assert "not metered" not in message  # instructions spell out this limitation explicitly
    assert note not in task_prompt(work, "agent_only", "wdbc", 42)
    with pytest.raises(BenchmarkError):
        add_memory(work, "agent_only", "wdbc", 42, result["id"], note)


def test_manifest_source_hash_stays_pinned(tmp_path: Path) -> None:
    pytest.importorskip("sklearn")
    work, vault = tmp_path / "work", tmp_path / "vault"
    setup(work, vault, "smoke", {}, False)
    data = work / "public" / "wdbc" / "train.csv"
    original = hashlib.sha256(data.read_bytes()).hexdigest()
    data.write_text(data.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert hashlib.sha256(data.read_bytes()).hexdigest() != original
    candidate = _write(tmp_path / "candidate.json", _candidate())
    with pytest.raises(BenchmarkError, match="public split was modified"):
        perform_trial(work, "offline_baseline", "wdbc", 42, candidate)


def test_real_pilot_uses_a_fresh_holdout_from_inspected_smoke(tmp_path: Path) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    smoke_work, smoke_vault = tmp_path / "smoke-work", tmp_path / "smoke-vault"
    pilot_work, pilot_vault = tmp_path / "pilot-work", tmp_path / "pilot-vault"
    setup(smoke_work, smoke_vault, "smoke", {}, False)
    setup(pilot_work, pilot_vault, "pilot", {}, True)
    smoke_meta = json.loads((smoke_work / "meta.json").read_text(encoding="utf-8"))
    pilot_meta = json.loads((pilot_work / "meta.json").read_text(encoding="utf-8"))
    assert smoke_meta["datasets"]["wdbc"]["split_seed"] == 20271025
    assert pilot_meta["datasets"]["wdbc"]["split_seed"] == 20272025
    assert smoke_meta["datasets"]["wdbc"]["files"]["test"]["sha256"] != (
        pilot_meta["datasets"]["wdbc"]["files"]["test"]["sha256"]
    )
