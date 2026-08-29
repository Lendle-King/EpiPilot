"""Stable JSON-friendly projections for Codex CLI and MCP surfaces."""

from __future__ import annotations

from epipilot.research.contracts import ResearchDirective
from epipilot.state.project import ProjectState


def state_payload(state: ProjectState) -> dict[str, object]:
    """Project canonical state into a compact user/tool-facing structure."""
    return {
        "project_id": state.project_id,
        "event_version": state.event_version,
        "requirements": [
            {"id": str(item.id), "kind": item.kind.value, "statement": item.statement}
            for item in state.requirements
        ],
        "decisions": [
            {
                "id": str(item.id),
                "question": item.question,
                "choice": item.choice,
                "authority": item.authority.value,
                "reversible": item.reversible,
            }
            for item in state.decisions
        ],
        "unknowns": [
            {
                "id": str(item.id),
                "question": item.question,
                "impact": item.impact.value,
                "resolution_mode": item.resolution_mode.value,
                "status": item.status.value,
                "value_of_information": item.value_of_information,
                "decision_sensitivity": item.decision_sensitivity,
                "resolution_evidence": [str(value) for value in item.resolution_evidence],
                "resolution_decisions": list(item.resolution_decisions),
            }
            for item in state.unknowns
        ],
        "hypotheses": [
            {
                "id": str(item.id),
                "statement": item.statement,
                "status": item.status.value,
                "confidence": item.confidence,
                "predictions": list(item.predictions),
                "falsification_conditions": list(item.falsification_conditions),
                "supporting_evidence": [str(value) for value in item.supporting_evidence],
                "contradicting_evidence": [str(value) for value in item.contradicting_evidence],
                "superseded_by": str(item.superseded_by) if item.superseded_by else None,
            }
            for item in state.hypotheses
        ],
        "experiments": [
            {
                "id": str(item.id),
                "unknown_id": str(item.contract.unknown_id),
                "objective": item.contract.objective,
                "hypothesis_ids": [str(value) for value in item.contract.hypothesis_ids],
                "controlled_variables": list(item.contract.controlled_variables),
                "measurements": list(item.contract.measurements),
                "predictions": [
                    {
                        "hypothesis_id": str(prediction.hypothesis_id),
                        "expected_observation": prediction.expected_observation,
                        "falsification_condition": prediction.falsification_condition,
                    }
                    for prediction in item.contract.predictions
                ],
                "decision_rule": item.contract.decision_rule,
                "budget": item.contract.budget,
                "resource_claims": list(item.contract.resource_claims),
                "status": item.status.value,
                "evidence_ids": [str(value) for value in item.evidence_ids],
                "conclusion": item.conclusion,
            }
            for item in state.experiments
        ],
        "evidence": [
            {
                "id": str(item.id),
                "kind": item.kind.value,
                "summary": item.summary,
                "source": item.provenance.source,
                "scope": item.provenance.scope,
                "independently_verified": item.independently_verified,
            }
            for item in state.evidence
        ],
        "tasks": [
            {"id": str(item.id), "objective": item.objective, "status": item.status.value}
            for item in state.tasks
        ],
        "current_plan_version": state.current_plan.version if state.current_plan else None,
    }


def directive_payload(directive: ResearchDirective) -> dict[str, object]:
    """Project the deterministic research frontier into a tool-facing structure."""
    synthesis = directive.synthesis_contract
    synthesis_payload: dict[str, object] | None = None
    if synthesis is not None:
        synthesis_payload = {
            "goal": synthesis.goal,
            "success_criteria": list(synthesis.success_criteria),
            "required_dimensions": [item.value for item in synthesis.required_dimensions],
            "exploration_questions": list(synthesis.exploration_questions),
        }

    return {
        "kind": directive.kind.value,
        "reason": directive.reason,
        "canonical_event_version": directive.canonical_event_version,
        "questions": list(directive.questions),
        "unknown_id": str(directive.unknown_id) if directive.unknown_id else None,
        "experiment_id": str(directive.experiment_id) if directive.experiment_id else None,
        "task_id": str(directive.task_id) if directive.task_id else None,
        "synthesis_contract": synthesis_payload,
    }
