"""Synthetic regression tests for the independent-root binary panel adapter."""

import pytest
from pydantic import ValidationError

from epipilot.topic_discovery.binary_panel import (
    BinaryObservation,
    compare_paired_conditions,
    summarize_binary_panel,
)


def trial(
    root: str, condition: str, repeat: int, success: bool, local: bool | None = None
) -> BinaryObservation:
    return BinaryObservation(
        root_id=root,
        condition=condition,
        repeat_id=str(repeat),
        success=success,
        local_predicate=local,
    )


def test_root_balancing_and_counterexamples() -> None:
    rows = [trial("a", "base", i, True, True) for i in range(8)]
    rows += [trial("b", "base", 0, False, True), trial("b", "base", 1, False)]
    report = summarize_binary_panel(rows)
    assert report.independent_roots == 2
    assert report.trials == 10
    assert report.root_balanced_rates == {"base": 0.5}
    assert report.local_predicate.counterexample_roots == ["b"]
    assert report.local_predicate.counterexample_trials == 1
    assert report.local_predicate.unknown_trials == 1


def test_paired_panel_does_not_count_seeds_as_independent_roots() -> None:
    rows = []
    for root, n, baseline in [("a", 8, False), ("b", 1, True)]:
        for repeat in range(n):
            rows += [trial(root, "base", repeat, baseline), trial(root, "repair", repeat, True)]
    report = compare_paired_conditions(rows, treatment="repair", control="base")
    assert report.root_balanced_net_gain == 0.5
    assert [root.pairs for root in report.roots] == [8, 1]
    assert report.roots[0].gains == 8
    assert report.roots[1].both_success == 1


def test_missing_pair_blocks_aggregate() -> None:
    rows = [trial("a", "base", 0, False), trial("a", "repair", 1, True)]
    report = compare_paired_conditions(rows, treatment="repair", control="base")
    assert report.unmatched_control == 1
    assert report.unmatched_treatment == 1
    assert report.root_balanced_net_gain is None


def test_duplicate_trial_is_not_a_new_observation() -> None:
    row = trial("a", "base", 0, True)
    with pytest.raises(ValueError, match="duplicate root"):
        summarize_binary_panel([row, row])


def test_unknown_outcome_is_not_silently_coerced() -> None:
    with pytest.raises(ValidationError):
        BinaryObservation.model_validate(
            {"root_id": "a", "condition": "base", "repeat_id": "0", "success": "unknown"}
        )


def test_comparison_retains_all_four_pair_outcomes() -> None:
    rows = []
    for repeat, (a, b) in enumerate([(True, False), (False, True), (True, True), (False, False)]):
        rows += [trial("a", "repair", repeat, a), trial("a", "base", repeat, b)]
    report = compare_paired_conditions(rows, treatment="repair", control="base")
    cell = report.roots[0]
    assert (cell.gains, cell.reversals, cell.both_success, cell.both_failure) == (1, 1, 1, 1)
    assert report.root_balanced_net_gain == 0
