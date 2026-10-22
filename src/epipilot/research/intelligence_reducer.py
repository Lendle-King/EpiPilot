"""Pure replay and authority gates for Scout metadata and frozen decisions."""

from dataclasses import replace

from epipilot.events.payloads import EventPayload
from epipilot.research.budget import BudgetManager
from epipilot.research.intelligence_models import (
    DecisionForecastRecorded,
    DecisionOutcomeRecorded,
    IntelligenceState,
    ResearchPolicyConfigured,
    ScoutCompleted,
    ScoutResultStatus,
    ScoutStarted,
)
from epipilot.research.models import OverheadReservation, OverheadSettlement, amounts
from epipilot.state.project import ProjectState


def reduce_intelligence(state: ProjectState, payload: EventPayload) -> ProjectState:
    r = state.research
    if r is None:
        raise ValueError("explicit canonical research goal required")
    intel = state.intelligence or IntelligenceState()
    if isinstance(payload, ResearchPolicyConfigured):
        p = payload.policy
        if not any(
            str(d.id) == p.basis_ref and d.authority.value == "user" for d in state.decisions
        ):
            raise ValueError("policy requires canonical user authority")
        if p.id in {old.id for old in intel.policies}:
            raise ValueError("duplicate policy id; append a new version")
        if intel.active or r.active:
            raise ValueError("recover active operations before policy changes")
        if not set(amounts(p.cost_scales)) <= BudgetManager(r).limits.keys():
            raise ValueError("policy cost units require existing research budget")
        intel = intel.model_copy(update={"policies": (*intel.policies, p)})
    elif isinstance(payload, ScoutStarted):
        q, p = payload.request, intel.policy
        if intel.active or r.active:
            raise ValueError("serial execution requires recovery first")
        if q.operation_id in {s.request.operation_id for s in intel.starts}:
            raise ValueError("duplicate scout operation")
        if q.policy_id != p.id or q.based_on_version != state.event_version + 1:
            raise ValueError("stale Scout policy/request version")
        if r.frontier is None or q.canonical_root != r.frontier.repository:
            raise ValueError("Scout canonical root mismatch")
        if q.reserved_cost != p.scout_cost or any(
            c.amount > BudgetManager(r).remaining[c.unit] for c in q.reserved_cost
        ):
            raise ValueError("Scout budget insufficient or reservation mismatch")
        if any(s.request.epoch == q.epoch for s in intel.starts):
            raise ValueError("unchanged Scout epoch; new evidence or strategy required")
        intel = intel.model_copy(update={"starts": (*intel.starts, payload)})
        r = r.model_copy(
            update={
                "overhead_reservations": (
                    *r.overhead_reservations,
                    OverheadReservation(operation_id=q.operation_id, costs=q.reserved_cost),
                )
            }
        )
    elif isinstance(payload, ScoutCompleted):
        start = next(
            (s.request for s in intel.active if s.request.operation_id == payload.operation_id),
            None,
        )
        if start is None or payload.request_version != start.based_on_version:
            raise ValueError("completion requires unique active Scout request")
        if set(amounts(payload.actual_cost)) != set(amounts(start.reserved_cost)):
            raise ValueError("actual Scout costs must measure every reserved unit")
        if payload.status is ScoutResultStatus.ACCEPTED:
            if r.frontier is None or payload.discovery_id not in {
                d.id for d in r.frontier.discoveries
            }:
                raise ValueError("accepted Scout requires atomic canonical discovery")
            if (
                len(payload.candidates) > intel.policy.max_candidates
                or len(payload.sources) > intel.policy.max_sources
                or len(payload.queries) > intel.policy.max_queries
            ):
                raise ValueError("Scout bounds exceeded")
            if len({c.id for c in payload.candidates}) != len(payload.candidates) or len(
                {e.proposal_id for e in payload.estimates}
            ) != len(payload.estimates):
                raise ValueError("duplicate candidate/estimate")
            if any(
                e.proposal_id not in {p.id for p in r.proposals}
                or any(
                    o.proposal_id is not None and o.proposal_id not in {p.id for p in r.proposals}
                    for o in e.options
                )
                or e.utility_version != intel.policy.utility_version
                for e in payload.estimates
            ):
                raise ValueError("estimate needs canonical proposal and configured utility")
        elif payload.candidates or payload.estimates or payload.discovery_id is not None:
            raise ValueError("rejected completion cannot register candidates or estimates")
        intel = intel.model_copy(update={"completions": (*intel.completions, payload)})
        r = r.model_copy(
            update={
                "overhead_settlements": (
                    *r.overhead_settlements,
                    OverheadSettlement(
                        operation_id=payload.operation_id, actual_cost=payload.actual_cost
                    ),
                )
            }
        )
    elif isinstance(payload, DecisionForecastRecorded):
        from epipilot.research.decision_policy import validate_forecast

        validate_forecast(state, payload.forecast)
        intel = intel.model_copy(update={"forecasts": (*intel.forecasts, payload.forecast)})
    elif isinstance(payload, DecisionOutcomeRecorded):
        from epipilot.research.decision_policy import outcome_for

        f = next((f for f in intel.forecasts if f.id == payload.outcome.forecast_id), None)
        if (
            f is None
            or f.id in {o.forecast_id for o in intel.outcomes}
            or outcome_for(state, f) != payload.outcome
        ):
            raise ValueError("outcome requires unique verified review/settlement correspondence")
        intel = intel.model_copy(update={"outcomes": (*intel.outcomes, payload.outcome)})
    else:
        raise ValueError("unsupported research intelligence event")
    return replace(state, intelligence=intel, research=r)


def validate_intelligence_boundary(state: ProjectState) -> None:
    """An atomic application/replay boundary cannot expose an unreserved forecast."""
    if state.intelligence is None:
        return
    r = state.research
    assert r is not None
    selected = [f.selected_proposal for f in state.intelligence.forecasts]
    if len(selected) != len(set(selected)):
        raise ValueError("one forecast per experiment reservation required")
    for f in state.intelligence.forecasts:
        if not any(v.proposal_id == f.selected_proposal and v.at == f.at for v in r.reservations):
            raise ValueError("forecast and reservation require an atomic batch")
