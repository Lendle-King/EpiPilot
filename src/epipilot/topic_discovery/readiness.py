"""Deterministic readiness checks for a candidate's first experiment."""

from __future__ import annotations

from epipilot.topic_discovery.models import (
    AuditVerdict,
    DiscoveryWorkspace,
    ExperimentReadiness,
)


def assess_experiment_readiness(workspace: DiscoveryWorkspace) -> ExperimentReadiness:
    """Check whether the selected idea has auditable novelty and an affordable design.

    The result only determines whether the package is ready for human review.
    It never launches an experiment or promotes a hypothesis to evidence.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    selected_id = workspace.selected_idea_id

    if selected_id is None:
        blockers.append("select one candidate idea before designing its first experiment")
        return ExperimentReadiness(
            ready_for_human_review=False,
            selected_idea_id=None,
            blockers=blockers,
            warnings=warnings,
        )

    audits = [audit for audit in workspace.novelty_audits if audit.idea_id == selected_id]
    if not audits:
        blockers.append("the selected idea has no prior-art audit")
    else:
        latest_audit = max(audits, key=lambda audit: audit.reviewed_at)
        if latest_audit.verdict is AuditVerdict.INSUFFICIENT_SEARCH:
            blockers.append("the latest prior-art audit reports insufficient search")
        elif latest_audit.verdict is AuditVerdict.OVERLAP_FOUND:
            blockers.append("the latest prior-art audit found a potentially overlapping work")
        if latest_audit.blocking_overlap_questions:
            blockers.extend(
                f"unresolved prior-art question: {question}"
                for question in latest_audit.blocking_overlap_questions
            )
        warnings.extend(
            f"residual prior-art risk: {risk}" for risk in latest_audit.overlap_risks
        )

    designs = [design for design in workspace.experiment_designs if design.idea_id == selected_id]
    if not designs:
        blockers.append("the selected idea has no minimal experiment design")
    else:
        latest_design = max(designs, key=lambda design: design.created_at)
        topic = workspace.topic
        if topic.budget_limit is None or topic.budget_unit is None:
            blockers.append("set an explicit topic budget limit and unit before experiment review")
        elif latest_design.cost_unit != topic.budget_unit:
            blockers.append(
                "experiment cost unit does not match the topic budget unit "
                f"({latest_design.cost_unit!r} != {topic.budget_unit!r})"
            )
        elif latest_design.estimated_cost > topic.budget_limit:
            blockers.append(
                "estimated experiment cost exceeds the topic budget "
                f"({latest_design.estimated_cost:g} > {topic.budget_limit:g} "
                f"{topic.budget_unit})"
            )

    return ExperimentReadiness(
        ready_for_human_review=not blockers,
        selected_idea_id=selected_id,
        blockers=blockers,
        warnings=warnings,
    )
