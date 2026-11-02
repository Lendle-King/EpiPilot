from __future__ import annotations

import pytest
from experiments.researchbench_r1.calibration import (
    classification_loss,
    progress_area,
    scheduling_loss,
)


def test_calibration_is_reproducible_and_reference_improves() -> None:
    for measure in (scheduling_loss, classification_loss):
        baseline = [measure(seed, "baseline") for seed in range(10)]
        reference = [measure(seed, "reference") for seed in range(10)]
        assert baseline == [measure(seed, "baseline") for seed in range(10)]
        assert sum(reference) < sum(baseline)


def test_unknown_candidate_is_rejected() -> None:
    for measure in (scheduling_loss, classification_loss):
        with pytest.raises(ValueError):
            measure(1, "other")


def test_progress_integrates_best_so_far_and_holds_early_stop() -> None:
    assert progress_area(10, [(2, 0.4), (5, 0.2), (8, 1.0)]) == pytest.approx(0.44)
    assert progress_area(10, []) == 0


@pytest.mark.parametrize(
    "budget,events",
    [
        (0, []),
        (10, [(-1, 0.5)]),
        (10, [(11, 0.5)]),
        (10, [(2, 1.1)]),
        (10, [(2, float("nan"))]),
        (10, [(5, 0.4), (3, 0.5)]),
    ],
)
def test_progress_rejects_invalid_measurements(
    budget: float, events: list[tuple[float, float]]
) -> None:
    with pytest.raises(ValueError):
        progress_area(budget, events)
