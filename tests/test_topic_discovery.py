"""Tests for the standalone topic-to-validated-project scaffold."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from epipilot.topic_discovery.models import (
    AuditVerdict,
    DiscoveryWorkspace,
    ExperimentDesign,
    IdeaCandidate,
    MetricDirection,
    NoveltyAudit,
    NoveltyDifference,
    NoveltyDimension,
    PaperReference,
    ResearchGap,
    TopicSpec,
)
from epipilot.topic_discovery.readiness import assess_experiment_readiness


def _workspace(
    *,
    budget_limit: float | None = 20,
    estimated_cost: float = 8,
    verdict: AuditVerdict = AuditVerdict.PROVISIONALLY_DISTINCT,
) -> DiscoveryWorkspace:
    topic = TopicSpec(
        id="topic-1",
        title="Embodied skill generalization",
        description="Study reliable composition and adaptation of embodied robot skills.",
        research_area="Embodied AI",
        target_venues=["ICML", "ICLR"],
        budget_limit=budget_limit,
        budget_unit="gpu_hours" if budget_limit is not None else None,
    )
    papers = [
        PaperReference(
            id="paper-1",
            title="A prior skill chaining study",
            year=2024,
            relevance_note="Studies composition through learned initiation sets.",
            abstract="We model physical contact as a state variable.",
            url="https://example.org/papers/skill-chaining",
        ),
        PaperReference(
            id="paper-2",
            title="A contact-aware manipulation study",
            year=2025,
            relevance_note="Models contact conditions for manipulation execution.",
            url="https://example.org/papers/contact-manipulation",
        ),
    ]
    gap = ResearchGap(
        id="gap-1",
        statement="Frozen skills have poorly characterized execution boundaries.",
        importance_rationale="Unreliable boundaries make long-horizon composition brittle.",
        why_unresolved="Existing evaluations do not isolate all relevant physical conditions.",
        supporting_source_ids=["paper-1", "paper-2"],
    )
    idea = IdeaCandidate(
        id="idea-1",
        gap_id="gap-1",
        title="Evidence-grounded skill competence boundaries",
        research_question="Can physical structure improve prediction of frozen-skill success?",
        hypothesis="Structured physical evidence improves prediction on held-out contact modes.",
        proposed_mechanism="The representation preserves contact variables that affect execution.",
        expected_contribution=(
            "A transferable, calibrated model of skill-specific execution conditions."
        ),
        alternative_explanations=["The gain comes only from additional input features."],
        falsification_conditions=[
            "Matched-feature critics perform equally on held-out conditions."
        ],
    )
    audit = NoveltyAudit(
        id="audit-1",
        idea_id="idea-1",
        verdict=verdict,
        search_platforms=["OpenAlex", "Semantic Scholar"],
        search_queries=["skill competence boundary", "contact-conditioned skill success"],
        searched_paper_ids=["paper-1", "paper-2"],
        nearest_work_ids=["paper-1"],
        differences=[
            NoveltyDifference(
                dimension=NoveltyDimension.MECHANISM,
                nearest_work_id="paper-1",
                prior_art_claim="The prior work composes skills using learned initiation sets.",
                proposed_difference=(
                    "The candidate predicts policy-specific competence under physical conditions."
                ),
                materiality_rationale=(
                    "The prediction target and evidence requirements differ materially."
                ),
                evidence_locator="Methods, Section 3",
                supporting_quote="We model physical contact as a state variable.",
            )
        ],
        overlap_risks=["The relationship to robust option initiation sets needs deeper review."],
        reviewed_at=datetime(2026, 11, 24, tzinfo=UTC),
    )
    design = ExperimentDesign(
        id="design-1",
        idea_id="idea-1",
        dataset_or_environment="A held-out contact-rich manipulation benchmark",
        primary_metric="Brier score",
        metric_direction=MetricDirection.MINIMIZE,
        baseline_refs=["matched-feature critic", "success-rate classifier"],
        controls=["same training roots", "same observations", "same model-size budget"],
        protocol="Train on fixed physical roots and evaluate on unseen contact modes.",
        hypothesis_prediction="The structured model has lower held-out Brier score.",
        alternative_predictions=["A matched-feature critic has equal or lower Brier score."],
        falsification_rule="Reject the mechanism claim if the matched critic is not worse.",
        held_out_conditions=["unseen contact mode", "unseen object instance"],
        estimated_cost=estimated_cost,
        cost_unit="gpu_hours",
    )
    return DiscoveryWorkspace(
        id="workspace-1",
        topic=topic,
        papers=papers,
        gaps=[gap],
        ideas=[idea],
        novelty_audits=[audit],
        experiment_designs=[design],
        selected_idea_id="idea-1",
    )


def test_provisionally_distinct_candidate_can_reach_human_review() -> None:
    report = assess_experiment_readiness(_workspace())
    assert report.ready_for_human_review is True
    assert report.blockers == []
    assert len(report.warnings) == 1
    assert "does not prove novelty" in report.qualification


def test_missing_budget_blocks_experiment_review() -> None:
    report = assess_experiment_readiness(_workspace(budget_limit=None))
    assert report.ready_for_human_review is False
    assert any("explicit topic budget" in blocker for blocker in report.blockers)


def test_overlapping_prior_art_blocks_experiment_review() -> None:
    report = assess_experiment_readiness(_workspace(verdict=AuditVerdict.OVERLAP_FOUND))
    assert report.ready_for_human_review is False
    assert any("found a potentially overlapping work" in blocker for blocker in report.blockers)


def test_over_budget_design_is_blocked() -> None:
    report = assess_experiment_readiness(_workspace(estimated_cost=21))
    assert report.ready_for_human_review is False
    assert any("exceeds the topic budget" in blocker for blocker in report.blockers)


def test_unselected_candidate_is_not_implicitly_promoted() -> None:
    workspace = _workspace().model_copy(update={"selected_idea_id": None})
    report = assess_experiment_readiness(workspace)
    assert report.ready_for_human_review is False
    assert report.selected_idea_id is None


def test_provisional_novelty_requires_search_and_nearest_neighbor_evidence() -> None:
    with pytest.raises(ValidationError, match="provisionally distinct audit requires"):
        NoveltyAudit(
            id="audit-bad",
            idea_id="idea-1",
            verdict=AuditVerdict.PROVISIONALLY_DISTINCT,
        )


def test_workspace_rejects_unresolved_source_references() -> None:
    workspace = _workspace().model_dump()
    workspace["gaps"][0]["supporting_source_ids"] = ["paper-does-not-exist"]
    with pytest.raises(ValidationError, match="references unknown papers"):
        DiscoveryWorkspace.model_validate(workspace)


def test_topic_budget_requires_a_unit() -> None:
    with pytest.raises(ValidationError, match="must be supplied together"):
        TopicSpec(
            id="topic-1",
            title="A sufficiently long topic",
            description="A sufficiently long research description for validation.",
            research_area="Machine Learning",
            budget_limit=4,
        )


def test_insufficient_search_blocks_experiment_review() -> None:
    report = assess_experiment_readiness(_workspace(verdict=AuditVerdict.INSUFFICIENT_SEARCH))
    assert report.ready_for_human_review is False
    assert any("insufficient search" in blocker for blocker in report.blockers)


def test_novelty_difference_must_reference_a_listed_nearest_work() -> None:
    workspace = _workspace().model_dump()
    workspace["novelty_audits"][0]["nearest_work_ids"] = ["paper-2"]
    with pytest.raises(ValidationError, match="listed nearest work"):
        DiscoveryWorkspace.model_validate(workspace)


def test_paper_reference_requires_a_traceable_locator() -> None:
    with pytest.raises(ValidationError, match="requires a DOI, arXiv ID, or URL"):
        PaperReference(
            id="paper-untraceable",
            title="Untraceable paper",
            year=2025,
            relevance_note="This fixture intentionally lacks a source locator.",
        )
