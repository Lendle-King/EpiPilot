"""Deterministic research-frontier policy for Codex and other interactive frontends."""

from __future__ import annotations

from epipilot.epistemics.models import (
    ResolutionMode,
    Unknown,
    UnknownImpact,
    UnknownStatus,
)
from epipilot.requirements.frontier import DecisionAction, DecisionQuestion, decide_action
from epipilot.requirements.models import ProjectContract, RequirementKind
from epipilot.research.contracts import (
    ExperimentStatus,
    GoalConditionedSynthesisContract,
    ResearchDirective,
    ResearchDirectiveKind,
)
from epipilot.state.project import ProjectState

_IMPACT_WEIGHT = {
    UnknownImpact.LOW: 1,
    UnknownImpact.MEDIUM: 2,
    UnknownImpact.HIGH: 3,
}


def choose_research_directive(
    contract: ProjectContract,
    state: ProjectState,
    *,
    pending_decisions: tuple[DecisionQuestion, ...] = (),
) -> ResearchDirective:
    """Choose the next bounded research action without treating model intuition as truth.

    The policy deliberately does not return an automatic ``ACCEPT`` action. When no open
    unknown or runnable task remains it returns ``SYNTHESIZE`` with a mandatory goal-conditioned
    epistemic report contract; project acceptance still belongs to the independent acceptance
    contract.
    """
    if contract.project_id != state.project_id:
        raise ValueError("project contract and canonical state refer to different projects")

    if not contract.execution_ready:
        return ResearchDirective(
            kind=ResearchDirectiveKind.ASK_USER,
            reason="the project contract lacks an explicit success criterion",
            canonical_event_version=state.event_version,
            questions=("What observable condition would count as project success?",),
        )

    blocking_questions = tuple(
        question.question
        for question in pending_decisions
        if decide_action(question) is DecisionAction.ASK_USER
    )
    if blocking_questions:
        return ResearchDirective(
            kind=ResearchDirectiveKind.ASK_USER,
            reason="high-impact user-owned decisions remain unresolved",
            canonical_event_version=state.event_version,
            questions=blocking_questions,
        )

    if state.current_plan is not None:
        runnable = state.current_plan.runnable_tasks()
        if runnable:
            return ResearchDirective(
                kind=ResearchDirectiveKind.EXECUTE,
                reason="a verified-precondition task is ready in the current canonical plan",
                canonical_event_version=state.event_version,
                task_id=runnable[0].id,
            )

    open_unknowns = tuple(
        unknown for unknown in state.unknowns if unknown.status is UnknownStatus.OPEN
    )
    user_unknowns = tuple(
        unknown.question
        for unknown in open_unknowns
        if unknown.resolution_mode is ResolutionMode.ASK_USER
    )
    if user_unknowns:
        return ResearchDirective(
            kind=ResearchDirectiveKind.ASK_USER,
            reason="an open unknown is explicitly user-owned",
            canonical_event_version=state.event_version,
            questions=user_unknowns,
        )

    safe_defaults = [
        unknown
        for unknown in open_unknowns
        if unknown.resolution_mode is ResolutionMode.SAFE_DEFAULT
    ]
    if safe_defaults:
        safe_defaults.sort(key=_unknown_priority)
        target = safe_defaults[0]
        return ResearchDirective(
            kind=ResearchDirectiveKind.USE_SAFE_DEFAULT,
            reason=(
                "an open unknown has an explicit reversible safe-default resolution mode; "
                "record the system decision before resolving it"
            ),
            canonical_event_version=state.event_version,
            unknown_id=target.id,
        )

    technical_unknowns = [
        unknown
        for unknown in open_unknowns
        if unknown.resolution_mode in {ResolutionMode.EXPERIMENT, ResolutionMode.INVESTIGATION}
    ]
    if technical_unknowns:
        technical_unknowns.sort(key=_unknown_priority)
        target = technical_unknowns[0]
        pending_experiments = tuple(
            experiment
            for experiment in state.experiments
            if experiment.contract.unknown_id == target.id
            and experiment.status is ExperimentStatus.PREREGISTERED
        )
        if pending_experiments:
            return ResearchDirective(
                kind=ResearchDirectiveKind.RUN_EXPERIMENT,
                reason=(
                    "the highest-priority technical unknown has a preregistered experiment "
                    "that has not yet produced a verified conclusion"
                ),
                canonical_event_version=state.event_version,
                unknown_id=target.id,
                experiment_id=pending_experiments[0].id,
            )
        return ResearchDirective(
            kind=ResearchDirectiveKind.INVESTIGATE,
            reason=(
                "an unresolved technical unknown has the highest current "
                "decision-weighted information value and needs a new discriminative experiment"
            ),
            canonical_event_version=state.event_version,
            unknown_id=target.id,
        )

    return ResearchDirective(
        kind=ResearchDirectiveKind.SYNTHESIZE,
        reason=(
            "no runnable task or open research unknown remains; before acceptance, synthesize "
            "both goal achievement and a goal-conditioned account of the subject's nature, "
            "hypothesis landscape, evidence, boundaries, and remaining uncertainty"
        ),
        canonical_event_version=state.event_version,
        synthesis_contract=_goal_conditioned_synthesis_contract(contract),
    )


def _goal_conditioned_synthesis_contract(
    contract: ProjectContract,
) -> GoalConditionedSynthesisContract:
    goal = next(
        item.statement for item in contract.requirements if item.kind is RequirementKind.GOAL
    )
    success_criteria = tuple(
        item.statement
        for item in contract.requirements
        if item.kind is RequirementKind.SUCCESS_CRITERION
    )
    return GoalConditionedSynthesisContract(goal=goal, success_criteria=success_criteria)


def _unknown_priority(unknown: Unknown) -> tuple[int, float, str]:
    """Return a deterministic sort key exposing impact and information value."""
    return (
        -_IMPACT_WEIGHT[unknown.impact],
        -(unknown.value_of_information * unknown.decision_sensitivity),
        str(unknown.id),
    )
