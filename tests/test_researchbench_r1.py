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


def test_trainable_shift_task_generalizes_better_with_reference() -> None:
    from experiments.researchbench_r1.tasks import (
        fit_classifier,
        generate_examples,
        score_classifier,
    )

    train = generate_examples(13, 500, 0.9)
    evaluation = generate_examples(71, 500, -0.3)
    baseline = fit_classifier(train, robust=False)
    reference = fit_classifier(train, robust=True)
    assert baseline == fit_classifier(train, robust=False)
    assert score_classifier(reference, evaluation) < score_classifier(baseline, evaluation)
    with pytest.raises(ValueError):
        fit_classifier([], robust=False)
    with pytest.raises(ValueError):
        score_classifier(reference, [])


def test_multi_resource_queue_completes_and_reference_improves() -> None:
    from experiments.researchbench_r1.tasks import generate_jobs, schedule

    jobs = generate_jobs(39, 150)
    baseline = schedule(jobs, "edf")
    reference = schedule(jobs, "weighted")
    assert baseline == schedule(generate_jobs(39, 150), "edf")
    assert baseline.jobs_completed == len(jobs)
    assert reference.jobs_completed == len(jobs)
    assert reference.mean_weighted_tardiness < baseline.mean_weighted_tardiness
    with pytest.raises(ValueError):
        schedule(jobs, "unsupported")
    with pytest.raises(ValueError):
        schedule([], "edf")


def test_resource_guard_fails_closed_for_invalid_or_excess_memory() -> None:
    from experiments.researchbench_r1.resource_guard import ResourceRefusal, assess_headroom

    assert assess_headroom(1000, 100).used_percent == 90
    with pytest.raises(ResourceRefusal):
        assess_headroom(1000, 49)
    with pytest.raises(ResourceRefusal):
        assess_headroom(0, 0)
    with pytest.raises(ResourceRefusal):
        assess_headroom(1000, 1001)


def test_multi_resource_contention_and_parallelism() -> None:
    from experiments.researchbench_r1.tasks import Job, schedule

    both_accelerated = (Job(0, 2, 0, 1, True), Job(0, 2, 0, 1, True))
    independent = (Job(0, 2, 0, 1, True), Job(0, 2, 0, 1, False))
    assert schedule(both_accelerated, "edf").mean_weighted_tardiness == 3
    assert schedule(independent, "edf").mean_weighted_tardiness == 2
    with pytest.raises(ValueError):
        schedule((Job(0, 0, 1, 1, True),), "edf")
