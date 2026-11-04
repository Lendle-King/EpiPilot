"""Engineering qualification only: synthetic data, no provider clients."""

from pathlib import Path

import pytest

from epipilot.benchfirst.offline import (
    EventKind,
    Journal,
    MockBudget,
    Prediction,
    PublicTask,
    evaluate_dev,
    freeze_candidate,
    strict_predictions,
)


def task() -> PublicTask:
    return PublicTask(
        task_id="synthetic-linear", seed=17, row_ids=("a", "b"), x=(1.0, 2.0), dev_y=(2.0, 4.0)
    )


def test_four_attempts_three_trials_crash_recovery(tmp_path: Path) -> None:
    budget = MockBudget(max_attempts=4, max_trials=3, wall_seconds=1800)
    ledger = Journal(tmp_path / "events.jsonl", budget, "cell-a", now=lambda: 10.0)
    ledger.start("model-0", EventKind.MODEL_STARTED)
    # Simulates a crash after durable reservation; never auto-retry the call.
    restored = Journal(ledger.path, budget, "cell-a", now=lambda: 20.0)
    with pytest.raises(ValueError, match="unreconciled"):
        restored.start("model-1", EventKind.MODEL_STARTED)
    restored.reconcile("model-0")
    for index in range(1, 4):
        restored.start(f"model-{index}", EventKind.MODEL_STARTED)
        restored.finish(f"model-{index}", failed=index == 2)
    for index in range(3):
        restored.start(f"trial-{index}", EventKind.TRIAL_STARTED, fingerprint=str(index))
        restored.finish(f"trial-{index}", failed=index == 1)
    with pytest.raises(ValueError, match="attempt budget"):
        restored.start("model-4", EventKind.MODEL_STARTED)
    with pytest.raises(ValueError, match="trial budget"):
        restored.start("trial-3", EventKind.TRIAL_STARTED, fingerprint="3")
    assert restored.counts() == (4, 3)
    assert sum(e.kind == EventKind.INTERRUPTED for e in restored.events()) == 1
    with pytest.raises(ValueError, match="already used"):
        restored.start("model-0", EventKind.MODEL_STARTED)


def test_duplicate_work_timeout_and_policy_identity(tmp_path: Path) -> None:
    clock = [0.0]
    budget = MockBudget(max_attempts=4, max_trials=3, wall_seconds=1800)
    ledger = Journal(tmp_path / "events.jsonl", budget, "cell-a", now=lambda: clock[0])
    ledger.start("t0", EventKind.TRIAL_STARTED, fingerprint="same-data-code-seed")
    ledger.finish("t0")
    with pytest.raises(ValueError, match="duplicate experiment"):
        ledger.start("t1", EventKind.TRIAL_STARTED, fingerprint="same-data-code-seed")
    with pytest.raises(ValueError, match="identity"):
        Journal(ledger.path, budget, "other-cell")
    with pytest.raises(ValueError, match="identity"):
        Journal(ledger.path, budget.model_copy(update={"max_attempts": 5}), "cell-a")
    clock[0] = 1800.01
    with pytest.raises(ValueError, match="wall budget"):
        ledger.start("m0", EventKind.MODEL_STARTED)
    assert ledger.events()[-1].kind == EventKind.REJECTED


def test_strict_ledger_corruption_fails_closed(tmp_path: Path) -> None:
    ledger = Journal(tmp_path / "events.jsonl", MockBudget(), "cell-a")
    with ledger.path.open("a") as handle:
        handle.write('{"sequence":')
    with pytest.raises(ValueError):
        ledger.events()


def test_prediction_boundary_and_independent_dev_score(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.py"
    candidate.write_text("print('synthetic')\n")
    frozen = freeze_candidate(candidate)
    result = Prediction(candidate_sha256=frozen.sha256, row_ids=("a", "b"), values=(2.0, 4.0))
    assert evaluate_dev(task(), frozen, result) == 0.0
    with pytest.raises(ValueError, match="row"):
        evaluate_dev(task(), frozen, result.model_copy(update={"row_ids": ("b", "a")}))
    with pytest.raises(ValueError, match="candidate"):
        evaluate_dev(task(), frozen, result.model_copy(update={"candidate_sha256": "f" * 64}))
    candidate.write_text("print('changed')\n")
    with pytest.raises(ValueError, match="digest"):
        evaluate_dev(task(), frozen, result)


@pytest.mark.parametrize(
    "payload", ["{}", '{"x":1,"x":2}', "null", '{"values":[NaN]}', '{"values":[1],"hidden":true}']
)
def test_malformed_predictions(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "result.json"
    path.write_text(payload)
    with pytest.raises(ValueError):
        strict_predictions(path)


def test_source_or_candidate_symlink_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.py"
    real.write_text("pass\n")
    link = tmp_path / "candidate.py"
    link.symlink_to(real)
    with pytest.raises(ValueError):
        freeze_candidate(link)


def test_only_reservations_are_spent_and_settlement_is_exactly_once(tmp_path: Path) -> None:
    ledger = Journal(tmp_path / "events.jsonl", MockBudget(), "cell-a")
    ledger.start("model-0", EventKind.MODEL_STARTED)
    ledger.finish("model-0", failed=True)
    with pytest.raises(ValueError, match="no pending"):
        ledger.finish("model-0")
    assert ledger.counts() == (1, 0)


def test_clock_rollback_and_nonfinite_prediction_rejected(tmp_path: Path) -> None:
    now = [20.0]
    ledger = Journal(tmp_path / "events.jsonl", MockBudget(), "cell-a", now=lambda: now[0])
    now[0] = 19.0
    with pytest.raises(ValueError, match="rollback"):
        ledger.start("m0", EventKind.MODEL_STARTED)
    with pytest.raises(ValueError):
        Prediction(candidate_sha256="a" * 64, row_ids=("a",), values=(float("inf"),))
