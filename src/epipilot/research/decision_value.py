"""Cost-aware, feasibility-masked expected value of information, adapted from Scout V1.

Pure functions operate on the *same* typed, evidence-gated ResearchState as the
current EpiPilot CLI. Forecasts are frozen at experiment pre-registration. Only qualified,
measurement-validated outcomes can calibrate later predictions.
"""

from __future__ import annotations

from math import isfinite

from epipilot.research.decision_models import (
    DecisionEstimate,
    DecisionForecast,
    DecisionOutcome,
    DecisionScore,
    OutcomeKind,
)
from epipilot.research.models import Proposal, ResearchState


def calibration_samples(
    estimate: DecisionEstimate, state: ResearchState
) -> tuple[tuple[DecisionForecast, DecisionOutcome], ...]:
    """Never use unverified, legacy, cancelled or mismatched samples."""
    return tuple(
        (forecast, actual)
        for contract_id, forecast in state.decision_forecasts.items()
        if (actual := state.decision_outcomes.get(contract_id)) is not None
        and actual.qualified
        and forecast.estimate.predictor == estimate.predictor
        and forecast.estimate.utility_version == estimate.utility_version
        and forecast.estimate.group == estimate.group
    )


def calibrate_probabilities(
    estimate: DecisionEstimate,
    samples: tuple[tuple[DecisionForecast, DecisionOutcome], ...],
    *,
    minimum_samples: int = 20,
    prior_strength: float = 20.0,
) -> tuple[float, ...]:
    """Bias-correct frozen categorical priors after enough validated outcomes."""
    prior = tuple(branch.probability for branch in estimate.branches)
    if minimum_samples < 1 or prior_strength <= 0:
        raise ValueError("calibration hyperparameters must be positive")
    if len(samples) < minimum_samples:
        return prior
    corrected = []
    for i, kind in enumerate(OutcomeKind):
        innovation = sum(
            float(observation.outcome is kind) - forecast.score.probabilities[i]
            for forecast, observation in samples
        )
        corrected.append(max(1e-9, prior[i] + innovation / (prior_strength + len(samples))))
    total = sum(corrected)
    return tuple(value / total for value in corrected)


def score_estimate(
    estimate: DecisionEstimate,
    probabilities: tuple[float, ...] | None = None,
    enabled_before: tuple[bool, ...] | None = None,
    enabled_after: tuple[tuple[bool, ...], ...] | None = None,
) -> float:
    """EVSI from the SAME calibrated joint table, never from stale prior utilities."""
    prob = (
        probabilities
        if probabilities is not None
        else tuple(branch.probability for branch in estimate.branches)
    )
    before = enabled_before if enabled_before is not None else (True,) * len(estimate.options)
    after = enabled_after if enabled_after is not None else (before,) * len(prob)
    if (
        len(prob) != len(estimate.branches)
        or len(before) != len(estimate.options)
        or len(after) != len(prob)
        or not any(before)
        or any(not 0 <= p <= 1 or not isfinite(p) for p in prob)
        or abs(sum(prob) - 1.0) > 1e-8
    ):
        raise ValueError("invalid probabilities or feasibility dimensions")
    prior_best = max(
        sum(p * branch.utilities[i] for p, branch in zip(prob, estimate.branches, strict=True))
        for i, enabled in enumerate(before)
        if enabled
    )
    posterior = 0.0
    for probability, branch, mask in zip(prob, estimate.branches, after, strict=True):
        if len(mask) != len(before) or not any(mask):
            raise ValueError("each outcome must preserve a non-executing feasible option")
        posterior += probability * max(
            utility for utility, enabled in zip(branch.utilities, mask, strict=True) if enabled
        )
    return posterior - prior_best


def _option_available(
    state: ResearchState,
    experiment: Proposal,
    option_id: str | None,
    outcome: OutcomeKind | None,
) -> bool:
    if option_id is None:
        return True
    option = state.proposals.get(option_id)
    if option is None or option.status != "proposed" or option.id == experiment.id:
        return False
    budget = state.remaining if outcome is None else state.remaining - experiment.estimated_cost
    if option.estimated_cost > budget + 1e-9:
        return False
    for required in option.depends_on:
        if required == experiment.id:
            if outcome is None or outcome is OutcomeKind.INFRASTRUCTURE_FAILURE:
                return False
        elif state.proposals[required].status != "completed":
            return False
    return True


def rank_decision(
    state: ResearchState,
    estimate: DecisionEstimate,
    *,
    minimum_samples: int = 20,
    prior_strength: float = 20.0,
    cost_penalty_scale: float = 1.0,
    risk_penalty_scale: float = 1.0,
) -> DecisionScore:
    """Score only a directly budget-feasible Proposal with a frozen decision table."""
    if (
        cost_penalty_scale < 0
        or risk_penalty_scale < 0
        or not isfinite(cost_penalty_scale)
        or not isfinite(risk_penalty_scale)
    ):
        raise ValueError("decision penalty scales must be finite and nonnegative")
    proposal = state.proposals.get(estimate.proposal_id)
    if proposal is None or proposal.status != "proposed":
        raise ValueError("decision estimate refers to an unavailable proposal")
    if state.goal is None or proposal.estimated_cost > state.remaining:
        raise ValueError("decision proposal exceeds approved remaining budget")
    if any(state.proposals[parent].status != "completed" for parent in proposal.depends_on):
        raise ValueError("decision proposal has unmet prerequisites")
    samples = calibration_samples(estimate, state)
    prob = calibrate_probabilities(
        estimate, samples, minimum_samples=minimum_samples, prior_strength=prior_strength
    )
    before = tuple(
        _option_available(state, proposal, option.proposal_id, None) for option in estimate.options
    )
    after = tuple(
        tuple(
            _option_available(state, proposal, option.proposal_id, outcome.outcome)
            for option in estimate.options
        )
        for outcome in estimate.branches
    )
    value = score_estimate(estimate, prob, before, after)
    cost = proposal.estimated_cost / state.goal.budget
    risk = 0.2 * proposal.risk * risk_penalty_scale
    return DecisionScore(
        proposal_id=proposal.id,
        value_of_information=value,
        normalized_cost=cost,
        risk_penalty=risk,
        net_value=value - (cost * cost_penalty_scale) - risk,
        probabilities=prob,
        calibration_samples=len(samples),
        enabled_before=before,
        enabled_after=after,
    )


def record_outcome(
    state: ResearchState,
    contract_id: str,
    evidence_id: str,
) -> DecisionOutcome | None:
    """Derive a forecast outcome ONLY from validated, matching v2 evidence."""
    forecast = state.decision_forecasts.get(contract_id)
    evidence = state.evidence.get(evidence_id)
    if forecast is None or evidence is None or not evidence.measurement_validated:
        return None
    experiment = state.experiments[evidence.experiment_id]
    if experiment.contract_id != contract_id:
        raise ValueError("forecast outcome evidence belongs to a different contract")
    kind = OutcomeKind(experiment.outcome)
    probabilities = forecast.score.probabilities
    brier = sum(
        (p - float(kind is outcome)) ** 2
        for p, outcome in zip(probabilities, OutcomeKind, strict=True)
    )
    return DecisionOutcome(
        contract_id=contract_id,
        evidence_id=evidence_id,
        outcome=kind,
        actual_cost=experiment.actual_cost,
        brier_score=brier,
        qualified=True,
    )
