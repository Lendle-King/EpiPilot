import csv
from pathlib import Path

import pytest

from epipilot.benchfirst.offline import digest
from epipilot.benchfirst.private_scoring import ClosedRun, LabelSet, SealedScorer, verify_score


def test_private_scorer_only_accepts_frozen_numeric_predictions(tmp_path: Path) -> None:
    prediction = tmp_path / "predictions.csv"
    prediction.write_text("row_id,prediction\na,2\nb,4\n")
    closed = ClosedRun(
        cell_id="cell",
        track="native",
        task_sha256="a" * 64,
        candidate_sha256="b" * 64,
        predictions_sha256=digest(prediction),
        closure_sha256="c" * 64,
    )
    labels = LabelSet(row_ids=("a", "b"), values=(1.0, 5.0))
    scorer = SealedScorer(labels, "d" * 64, b"k" * 32)
    score = scorer.score(closed, prediction)
    assert score.rmse == 1
    assert verify_score(score, b"k" * 32)
    assert not verify_score(score.model_copy(update={"rmse": 0.0}), b"k" * 32)
    prediction.write_text("row_id,prediction\na,1\nb,5\n")
    with pytest.raises(ValueError, match="digest"):
        scorer.score(closed, prediction)


@pytest.mark.parametrize(
    "rows",
    [
        [("a", "nan"), ("b", "4")],
        [("a", "2"), ("a", "4")],
        [("b", "4"), ("a", "2")],
        [("a", "__import__('os')"), ("b", "4")],
    ],
)
def test_hostile_predictions_are_rejected(tmp_path: Path, rows: list[tuple[str, str]]) -> None:
    prediction = tmp_path / "predictions.csv"
    with prediction.open("w") as handle:
        writer = csv.writer(handle)
        writer.writerow(("row_id", "prediction"))
        writer.writerows(rows)
    closed = ClosedRun(
        cell_id="cell",
        track="matched",
        task_sha256="a" * 64,
        candidate_sha256="b" * 64,
        predictions_sha256=digest(prediction),
        closure_sha256="c" * 64,
    )
    scorer = SealedScorer(LabelSet(row_ids=("a", "b"), values=(1.0, 5.0)), "d" * 64, b"k" * 32)
    with pytest.raises(ValueError):
        scorer.score(closed, prediction)
