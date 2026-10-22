from uuid import uuid4

import pytest
from pydantic import ValidationError


def estimate(irrelevant=False):
    from epipilot.research.intelligence_models import (
        BranchKind,
        DecisionBranch,
        DecisionEstimate,
        DecisionOption,
    )

    return DecisionEstimate(
        proposal_id="check",
        predictor="synthetic-v1",
        utility_version="goal-v1",
        options=(
            DecisionOption(key="continue", baseline_utility=0.5),
            DecisionOption(key="stop", baseline_utility=0.4),
        ),
        branches=tuple(
            DecisionBranch(
                outcome=kind,
                probability=0.25,
                utilities=(0.5, 0.4) if irrelevant else values,
            )
            for kind, values in zip(
                BranchKind, ((0.8, 0.2), (0.2, 0.6), (0.5, 0.4), (0.5, 0.4)), strict=True
            )
        ),
    )


def test_irrelevant_information_has_zero_decision_value():
    from epipilot.research.decision_value import score_estimate

    assert score_estimate(estimate(True)) == pytest.approx(0.0)


def test_discriminating_information_has_positive_decision_value():
    from epipilot.research.decision_value import score_estimate

    assert score_estimate(estimate()) == pytest.approx(0.1)


def test_unavailable_downstream_option_cannot_earn_unlock_value():
    from epipilot.research.decision_value import score_estimate

    assert score_estimate(
        estimate(), enabled_before=(True, False), enabled_after=((True, False),) * 4
    ) == pytest.approx(0.0)


def test_incoherent_prior_and_duplicate_outcomes_rejected():
    e = estimate()
    data = e.model_dump()
    data["options"] = (data["options"][0].copy(), data["options"][1].copy())
    data["options"][0]["baseline_utility"] = 0.9
    with pytest.raises(ValidationError, match="consistent"):
        type(e).model_validate(data)
    with pytest.raises(ValidationError, match="unique"):
        type(e)(**{**e.model_dump(), "branches": (e.branches[0],) * 4})


def test_probability_calibration_has_cold_start_and_no_cross_predictor_learning():
    from epipilot.research.decision_value import calibrate_probabilities
    from epipilot.research.intelligence_models import BranchKind, CalibrationSample

    e = estimate()
    sample = CalibrationSample(
        predictor=e.predictor,
        utility_version=e.utility_version,
        group="diagnostic:0:integrity",
        probabilities=(0.25,) * 4,
        outcome=BranchKind.REFUTE,
        forecast_id=uuid4(),
    )
    assert calibrate_probabilities(e, (sample,) * 19, group=sample.group) == (0.25,) * 4
    p = calibrate_probabilities(e, (sample,) * 20, group=sample.group)
    assert sum(p) == pytest.approx(1.0)
    assert p[1] > 0.25
    other = sample.model_copy(update={"predictor": "different"})
    assert calibrate_probabilities(e, (other,) * 20, group=sample.group) == (0.25,) * 4


def test_table_and_policy_bounds_are_strict():
    from epipilot.research.intelligence_models import DecisionEstimate, ResearchPolicy

    with pytest.raises(ValidationError):
        DecisionEstimate(**{**estimate().model_dump(), "options": estimate().options * 2})
    with pytest.raises(ValidationError):
        ResearchPolicy(id="p", basis_ref="authority", cost_scales=(), scout_cost=())
