"""Pure readiness and phase checks for the opt-in problem exploration mode.

The gate cannot turn a Scout citation or an agent's claim into scientific evidence.
"""

from __future__ import annotations

from epipilot.research.models import (
    ClaimStatus,
    ContractStatus,
    ExplorationStage,
    Proposal,
    ProposalKind,
    ResearchState,
)
from epipilot.research.scout_models import ScoutStatus

IMPORTANT_UNKNOWN_THRESHOLD = 0.25


def important_open_unknowns(state: ResearchState) -> list[str]:
    """Only decision-relevant, unresolved questions block automatic advancement."""
    return sorted(
        unknown.id
        for unknown in state.unknowns.values()
        if unknown.status == "open"
        and unknown.importance * unknown.decision_sensitivity >= IMPORTANT_UNKNOWN_THRESHOLD
    )


def qualified_exploration_evidence(state: ResearchState) -> list[str]:
    """Count only conclusive, reviewed exploratory experiments, never report snippets."""
    qualified: list[str] = []
    for evidence in state.evidence.values():
        if not evidence.measurement_validated or evidence.scientific_verdict == "inconclusive":
            continue
        experiment = state.experiments[evidence.experiment_id]
        if state.proposals[experiment.proposal_id].kind is not ProposalKind.EXPLORE:
            continue
        if any(
            claim.status in {ClaimStatus.SUPPORTED, ClaimStatus.REFUTED}
            and evidence.id in (
                claim.supporting_evidence_ids + claim.contradicting_evidence_ids
            )
            for claim in state.claims.values()
        ):
            qualified.append(evidence.id)
    return sorted(qualified)


def pending_research(state: ResearchState) -> list[str]:
    """Do not change phases while a frozen study or Scout is in flight."""
    pending = [
        contract.id
        for contract in state.contracts.values()
        if contract.status in {
            ContractStatus.PREREGISTERED,
            ContractStatus.RUNNING,
            ContractStatus.AWAITING_VERIFICATION,
        }
    ]
    pending.extend(
        run.id
        for run in state.scout_runs.values()
        if run.status is ScoutStatus.RUNNING
    )
    return sorted(pending)


def unseen_critical_reopenings(state: ResearchState) -> list[str]:
    """A new, important counterexample must be reconsidered before more optimization."""
    config = state.exploration
    if config is None or not config.enabled or config.stage is not ExplorationStage.OPTIMIZATION:
        return []
    seen = set(config.acknowledged_reopenings)
    return sorted(
        {
            evidence_id
            for unknown in state.unknowns.values()
            if unknown.status == "open"
            and unknown.importance * unknown.decision_sensitivity >= IMPORTANT_UNKNOWN_THRESHOLD
            for evidence_id in unknown.reopening_evidence_ids
            if evidence_id not in seen
        }
    )


def readiness(state: ResearchState) -> dict[str, object]:
    """Conservative readiness gate; budget exhaustion never establishes knowledge."""
    config = state.exploration
    if config is None or not config.enabled:
        return {"enabled": False, "status": "disabled"}
    important = important_open_unknowns(state)
    qualified = qualified_exploration_evidence(state)
    pending = pending_research(state)
    ready = bool(qualified) and not important and not pending
    if ready:
        status = "ready"
    elif pending:
        status = "pending_verification_or_execution"
    elif state.exploration_remaining <= 1e-9:
        status = "budget_limited"
    elif not state.unknowns:
        status = "frontier_required"
    else:
        status = "exploring"
    return {
        "enabled": True,
        "stage": config.stage.value,
        "status": status,
        "ready": ready,
        "important_open_unknown_ids": important,
        "qualified_exploration_evidence_ids": qualified,
        "pending_operation_ids": pending,
        "exploration_budget_limit": (
            state.goal.budget * config.budget_share if state.goal is not None else 0.0
        ),
        "exploration_spent": state.exploration_spent,
        "exploration_reserved": state.exploration_reserved,
        "exploration_remaining": state.exploration_remaining,
        "reconsideration_evidence_ids": unseen_critical_reopenings(state),
        "qualification": (
            "Readiness is an operational gate, not proof that the problem is fully understood. "
            "Exploratory evidence requires independent measurement and linked claim review."
        ),
    }


def ensure_action_allowed(state: ResearchState, proposal: Proposal) -> None:
    """Enforce the same gate in planning, pre-registration, and execution."""
    config = state.exploration
    if config is None or not config.enabled:
        return
    if (
        config.stage is ExplorationStage.EXPLORATION
        and proposal.kind is not ProposalKind.EXPLORE
    ):
        raise ValueError("exploration phase blocks optimization/verification proposals")
    if (
        config.stage is ExplorationStage.OPTIMIZATION
        and proposal.kind is not ProposalKind.EXPLORE
        and unseen_critical_reopenings(state)
    ):
        raise ValueError("new counterevidence requires exploration reopen or explicit advance")
