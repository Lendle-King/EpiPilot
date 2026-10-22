"""Decision-aware CARTS selection and canonical forecast/outcome compilation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from epipilot.research.engine import ResearchEngine

from epipilot.core.events import EventType
from epipilot.research.budget import BudgetManager
from epipilot.research.decision_value import calibrate_probabilities, score_estimate
from epipilot.research.intelligence_models import (
    BranchKind,
    CalibrationSample,
    DecisionEstimate,
    DecisionForecast,
    DecisionOutcome,
    DecisionOutcomeRecorded,
    DecisionScore,
)
from epipilot.research.models import (
    ExecutionStatus,
    ExperimentProposal,
    Outcome,
    Ranked,
    Ranking,
    Rejected,
    Strategy,
    amounts,
)
from epipilot.research.policy import goal_met, infeasible_reason
from epipilot.state.project import ProjectState


def calibration_samples(state: ProjectState) -> tuple[CalibrationSample, ...]:
    intel = state.intelligence
    if intel is None or state.research is None:
        return ()
    result = []
    for outcome in intel.outcomes:
        f = next(f for f in intel.forecasts if f.id == outcome.forecast_id)
        estimate = next(e for e in f.estimates if e.proposal_id == outcome.proposal_id)
        p = state.research.proposal(outcome.proposal_id)
        result.append(
            CalibrationSample(
                predictor=estimate.predictor,
                utility_version=estimate.utility_version,
                group=group_for(p),
                probabilities=tuple(b.probability for b in estimate.branches),
                outcome=outcome.outcome,
                forecast_id=f.id,
            )
        )
    return tuple(result)


def group_for(p: ExperimentProposal) -> str:
    return f"{p.kind.value}:{int(p.fidelity)}:{p.scope.value}"


def calibration_id(state: ProjectState) -> str:
    intel = state.intelligence
    data = {
        "algorithm": "smoothed-probability-bias-v1",
        "policy": intel.policy.id if intel else None,
        "outcomes": [str(o.forecast_id) for o in intel.outcomes] if intel else [],
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def decision_masks(
    state: ProjectState,
    proposal: ExperimentProposal,
    estimate: DecisionEstimate,
    at: datetime,
    registered: frozenset[str],
) -> tuple[tuple[bool, ...], tuple[tuple[bool, ...], ...]]:
    r = state.research
    assert r is not None and r.frontier is not None
    manager = BudgetManager(r)
    frontier = r.frontier

    def enabled(option_id: str | None, branch: BranchKind | None) -> bool:
        if option_id is None:
            return True  # Non-executing choices cannot invent an unlocked experiment.
        child = next((p for p in r.proposals if p.id == option_id), None)
        if child is None or child.id == proposal.id or not child.data_qualified:
            return False
        contract = next((c for c in frontier.contracts if c.proposal_id == child.id), None)
        if contract is None or contract.adapter not in registered:
            return False
        # Value only actual future options; never fabricate a runnable child or fidelity result.
        if child.id in {x.proposal_id for x in r.reservations} or child.predecessor_ids:
            return False
        assumed = (
            frozenset({proposal.hypothesis_id}) if branch is BranchKind.SUPPORT else frozenset()
        )
        from epipilot.research.agent_policy import agent_contract_reason
        from epipilot.research.reducer import task_id

        completed = (
            frozenset({task_id(state.project_id, proposal.id)})
            if branch in {BranchKind.SUPPORT, BranchKind.REFUTE, BranchKind.INCONCLUSIVE}
            else frozenset()
        )
        if infeasible_reason(
            state, child, at, assumed_supported=assumed, assumed_completed=completed
        ):
            return False
        if agent_contract_reason(state, child, registered, assumed_supported=assumed):
            return False
        try:
            combined = amounts(manager.reservation(proposal))
            for cost in manager.reservation(child):
                combined[cost.unit] = combined.get(cost.unit, 0.0) + cost.amount
            return all(value <= manager.remaining[unit] for unit, value in combined.items())
        except ValueError:
            return False

    before = tuple(enabled(o.proposal_id, None) for o in estimate.options)
    after = tuple(
        tuple(enabled(o.proposal_id, b.outcome) for o in estimate.options)
        for b in estimate.branches
    )
    return before, after


def score_candidate(
    state: ProjectState, estimate: DecisionEstimate, at: datetime, registered: frozenset[str]
) -> DecisionScore:
    intel, r = state.intelligence, state.research
    assert intel is not None and r is not None
    policy, p = intel.policy, r.proposal(estimate.proposal_id)
    if estimate.utility_version != policy.utility_version:
        raise ValueError("estimate utility version differs from policy")
    from epipilot.research.reducer import canonical_basis

    if any(ref not in canonical_basis(state) for ref in estimate.basis_refs):
        raise ValueError("estimate refers to noncanonical basis")
    probabilities = calibrate_probabilities(
        estimate,
        calibration_samples(state),
        group=group_for(p),
        minimum_samples=policy.probability_min_samples,
        prior_strength=policy.probability_prior_strength,
    )
    before, after = decision_masks(state, p, estimate, at, registered)
    value = score_estimate(estimate, probabilities, before, after)
    costs = BudgetManager(r).estimate(p)
    scales, weights = amounts(policy.cost_scales), amounts(policy.cost_weights)
    if not set(amounts(costs)) <= scales.keys():
        raise ValueError("experiment costs lack fixed policy scale")
    normalized = sum(c.amount / scales[c.unit] * weights.get(c.unit, 1.0) for c in costs)
    risk = policy.risk_weight * p.risk
    return DecisionScore(
        proposal_id=p.id,
        value=value,
        normalized_cost=normalized,
        risk=risk,
        score=policy.information_weight * value - normalized - risk,
        probabilities=probabilities,
        raw_cost=p.estimated_cost,
        estimated_cost=costs,
        enabled_before=before,
        enabled_after=after,
    )


def registered_contracts(state: ProjectState) -> frozenset[str]:
    r = state.research
    return frozenset(c.adapter for c in r.frontier.contracts) if r and r.frontier else frozenset()


def rank_decision_value(
    state: ProjectState, at: datetime, registered: frozenset[str] | None = None
) -> Ranking:
    if at.tzinfo is None:
        raise ValueError("decision time must be timezone-aware")
    r, intel = state.research, state.intelligence
    if r is None or r.frontier is None or intel is None:
        raise ValueError("configured canonical research policy/frontier required")
    registered = registered if registered is not None else registered_contracts(state)
    ranked, rejected = [], []
    estimates = {e.proposal_id: e for e in intel.estimates}
    for p in r.proposals:
        reason = infeasible_reason(state, p, at)
        from epipilot.research.agent_policy import agent_contract_reason

        if reason is None:
            reason = agent_contract_reason(state, p, registered)
        if reason is None and p.id not in estimates:
            reason = "fresh decision estimate required; run Scout"
        if reason:
            rejected.append(Rejected(proposal_id=p.id, reason=reason))
            continue
        try:
            s = score_candidate(state, estimates[p.id], at, registered)
        except ValueError:
            rejected.append(Rejected(proposal_id=p.id, reason="ineligible decision estimate/scale"))
            continue
        if s.score <= 0:
            rejected.append(
                Rejected(proposal_id=p.id, reason="nonpositive estimated net decision value")
            )
            continue
        ranked.append(
            Ranked(
                proposal_id=p.id,
                score=s.score,
                information=s.value,
                contribution=0.0,
                unlocking=0.0,
                exploration=0.0,
                risk=s.risk,
                normalized_cost=s.normalized_cost,
                source=calibration_id(state),
                confidence=p.estimate_confidence,
                uncertainty=p.uncertainty,
                explanation=(
                    "decision value heuristic; unlocking included once; not calibrated EVSI"
                ),
            )
        )
    ranked.sort(key=lambda x: (-x.score, x.proposal_id))
    # Keep the existing declared exploration protection as an explicit choice constraint.
    from epipilot.research.frontier import Purpose

    by_contract = {c.proposal_id: c for c in r.frontier.contracts}
    explore = [x for x in ranked if by_contract[x.proposal_id].purpose is Purpose.EXPLORE]
    if ranked and explore and by_contract[ranked[0].proposal_id].purpose is not Purpose.EXPLORE:
        manager = BudgetManager(r)
        total = spent = 0.0
        for settlement in r.settlements:
            spent_share = sum(c.amount / manager.limits[c.unit] for c in settlement.actual_cost)
            total += spent_share
            if (
                by_contract.get(settlement.proposal_id)
                and by_contract[settlement.proposal_id].purpose is Purpose.EXPLORE
            ):
                spent += spent_share
        left = manager.remaining.copy()
        for cost in manager.reservation(r.proposal(ranked[0].proposal_id)):
            left[cost.unit] -= cost.amount
        starves = any(
            c.amount > left[c.unit] for c in manager.reservation(r.proposal(explore[0].proposal_id))
        )
        if spent + 1e-12 < r.frontier.exploration_fraction * total or starves:
            chosen = explore[0].model_copy(
                update={
                    "explanation": explore[0].explanation + "; protect declared exploration budget"
                }
            )
            ranked = [chosen, *[x for x in ranked if x.proposal_id != chosen.proposal_id]]
    stop = None
    if goal_met(state):
        stop = "goal_met"
    elif r.active or intel.active:
        stop = "in_flight_requires_resume"
    elif not ranked:
        stop = "no_positive_feasible_decision_value"
    if stop in {"goal_met", "in_flight_requires_resume"}:
        ranked = []
    return Ranking(
        strategy=Strategy.CARTS,
        at=at,
        ranked=tuple(ranked),
        rejected=tuple(rejected),
        stop_reason=stop,
    )


def make_forecast(
    state: ProjectState, selected: str, at: datetime, revision: str, registered: frozenset[str]
) -> DecisionForecast:
    from epipilot.research.engine import stable_id

    intel = state.intelligence
    assert intel is not None
    from epipilot.research.scout import research_epoch

    epoch = research_epoch(state, revision)
    matching = [
        start.request
        for start in intel.starts
        if start.request.policy_id == intel.policy.id
        and start.request.git_revision == revision
        and start.request.epoch == epoch
        and any(
            c.operation_id == start.request.operation_id and c.status.value == "accepted"
            for c in intel.completions
        )
    ]
    if not matching:
        raise ValueError("forecast revision/epoch requires a fresh accepted Scout request")
    if any(f.selected_proposal == selected for f in intel.forecasts):
        raise ValueError("forecast already exists for this experiment reservation")
    ranking = rank_decision_value(state, at, registered)
    if not ranking.ranked or ranking.ranked[0].proposal_id != selected:
        raise ValueError("selected experiment differs from current decision policy")
    ids = {x.proposal_id for x in ranking.ranked[: intel.policy.max_candidates]}
    estimates = tuple(e for e in intel.estimates if e.proposal_id in ids)
    return DecisionForecast(
        id=stable_id(state.project_id, f"forecast:{state.event_version}:{selected}"),
        policy_id=intel.policy.id,
        based_on_version=state.event_version,
        git_revision=revision,
        at=at,
        estimates=estimates,
        scores=tuple(score_candidate(state, e, at, registered) for e in estimates),
        selected_proposal=selected,
        selection_reason=ranking.ranked[0].explanation,
        calibration_id=calibration_id(state),
        registered_adapters=tuple(sorted(registered)),
    )


def validate_forecast(state: ProjectState, forecast: DecisionForecast) -> None:
    if state.intelligence is None or forecast.based_on_version != state.event_version:
        raise ValueError("stale forecast")
    if forecast != make_forecast(
        state,
        forecast.selected_proposal,
        forecast.at,
        forecast.git_revision,
        frozenset(forecast.registered_adapters),
    ):
        raise ValueError("forecast differs from canonical policy/estimates")


def outcome_for(state: ProjectState, forecast: DecisionForecast) -> DecisionOutcome | None:
    r = state.research
    assert r is not None
    review = r.review(forecast.selected_proposal)
    settlement = next(
        (s for s in r.settlements if s.proposal_id == forecast.selected_proposal), None
    )
    observation = r.observation(forecast.selected_proposal)
    if review is None or settlement is None or observation is None:
        return None
    assessment = review.assessment
    p = r.proposal(forecast.selected_proposal)
    adequate = (
        assessment.verified
        and assessment.reliability >= p.minimum_reliability
        and observation.samples >= p.minimum_samples
    )
    from epipilot.research.frontier import Quality

    adequate = (
        adequate
        and r.frontier is not None
        and all(
            assessment.quality >= max(q.minimum_quality, Quality.MEASUREMENT)
            for q in r.frontier.questions
            if q.unknown_id in p.unknown_ids
        )
    )
    if observation.execution is not ExecutionStatus.SUCCEEDED or not assessment.verified:
        branch = BranchKind.FAILURE
    elif adequate and assessment.outcome is Outcome.SUPPORT:
        branch = BranchKind.SUPPORT
    elif adequate and assessment.outcome is Outcome.REFUTE and p.definitive_negative:
        branch = BranchKind.REFUTE
    else:
        branch = BranchKind.INCONCLUSIVE
    score = next(s for s in forecast.scores if s.proposal_id == p.id)
    estimate = next(e for e in forecast.estimates if e.proposal_id == p.id)
    index = tuple(BranchKind).index(branch)
    baseline = max(
        (i for i, v in enumerate(score.enabled_before) if v),
        key=lambda i: sum(
            w * b.utilities[i] for w, b in zip(score.probabilities, estimate.branches, strict=True)
        ),
    )
    utilities = estimate.branches[index].utilities
    model_gain = (
        max(u for u, allowed in zip(utilities, score.enabled_after[index], strict=True) if allowed)
        - utilities[baseline]
    )
    unlocked = []
    for option, was in zip(estimate.options, score.enabled_before, strict=True):
        if option.proposal_id is not None and not was:
            child = next((p for p in r.proposals if p.id == option.proposal_id), None)
            if (
                child is not None
                and infeasible_reason(state, child, observation.recorded_at) is None
            ):
                unlocked.append(child.id)
    return DecisionOutcome(
        forecast_id=forecast.id,
        proposal_id=p.id,
        evidence_id=review.evidence_id,
        outcome=branch,
        actual_cost=settlement.actual_cost,
        brier_score=sum(
            (prob - float(kind is branch)) ** 2
            for prob, kind in zip(score.probabilities, BranchKind, strict=True)
        ),
        model_implied_gain=model_gain,
        unlocked_proposal_ids=tuple(unlocked),
    )


def reconcile_decision_outcomes(engine: ResearchEngine) -> tuple[DecisionOutcome, ...]:
    # Import at the service boundary to keep domain models free of runtime dependencies.
    from epipilot.research.engine import ResearchEngine

    if not isinstance(engine, ResearchEngine):
        raise TypeError("canonical ResearchEngine required")
    intel = engine.state.intelligence
    if intel is None:
        return ()
    written = []
    for forecast in intel.forecasts:
        current_intel = engine.state.intelligence
        assert current_intel is not None
        if forecast.id in {o.forecast_id for o in current_intel.outcomes}:
            continue
        outcome = outcome_for(engine.state, forecast)
        if outcome is not None:
            engine.append(
                EventType.DECISION_OUTCOME_RECORDED, DecisionOutcomeRecorded(outcome=outcome)
            )
            written.append(outcome)
    return tuple(written)
