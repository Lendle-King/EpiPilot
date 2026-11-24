"""Typed contracts for topic-level scientific research discovery.

These records are deliberately separate from the existing project-level
ResearchState. They describe provisional discovery artifacts, not canonical
scientific facts or validated experiment evidence.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from epipilot.core.models import utc_now


class DiscoveryRecord(BaseModel):
    """Strict serializable model used by the topic-discovery subsystem."""

    model_config = ConfigDict(extra="forbid")


class LiteratureProvider(StrEnum):
    OPENALEX = "openalex"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    ARXIV = "arxiv"
    OPENREVIEW = "openreview"


class PaperSourceKind(StrEnum):
    PAPER = "paper"
    PREPRINT = "preprint"
    TECHNICAL_REPORT = "technical_report"
    CODE = "code"
    DATASET = "dataset"


class NoveltyDimension(StrEnum):
    PROBLEM = "problem"
    METHOD = "method"
    MECHANISM = "mechanism"
    SCENARIO = "scenario"
    INSIGHT = "insight"


class MetricDirection(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class AuditVerdict(StrEnum):
    INSUFFICIENT_SEARCH = "insufficient_search"
    OVERLAP_FOUND = "overlap_found"
    PROVISIONALLY_DISTINCT = "provisionally_distinct"


class TopicSpec(DiscoveryRecord):
    """User-approved scope and resource envelope for one discovery run."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=3)
    description: str = Field(min_length=20)
    research_area: str = Field(min_length=2)
    target_venues: list[str] = Field(default_factory=list)
    scope_constraints: list[str] = Field(default_factory=list)
    literature_start_year: int | None = Field(default=None, ge=1900, le=2200)
    literature_end_year: int | None = Field(default=None, ge=1900, le=2200)
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    budget_unit: str | None = None

    @model_validator(mode="after")
    def validate_scope_and_budget(self) -> Self:
        if (
            self.literature_start_year is not None
            and self.literature_end_year is not None
            and self.literature_start_year > self.literature_end_year
        ):
            raise ValueError("literature_start_year must not exceed literature_end_year")
        if (self.budget_limit is None) != (self.budget_unit is None):
            raise ValueError("budget_limit and budget_unit must be supplied together")
        if self.budget_unit is not None and not self.budget_unit.strip():
            raise ValueError("budget_unit must not be blank")
        return self


class PaperReference(DiscoveryRecord):
    """A traceable literature or technical source used by the discovery run."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=3)
    year: int = Field(ge=1900, le=2200)
    source_kind: PaperSourceKind = PaperSourceKind.PAPER
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    url: str | None = None
    relevance_note: str = Field(min_length=8)
    abstract: str | None = Field(default=None, max_length=100_000)
    authors: list[str] = Field(default_factory=list, max_length=200)
    publication_date: str | None = None
    keywords: list[str] = Field(default_factory=list, max_length=200)
    pdf_url: str | None = None
    provider_ids: list[PaperIdentifier] = Field(default_factory=list)
    provider_metrics: list[ProviderMetrics] = Field(default_factory=list)
    references: list[PaperIdentifier] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def require_source_locator(self) -> Self:
        if not any((self.doi, self.arxiv_id, self.url)):
            raise ValueError("paper reference requires a DOI, arXiv ID, or URL")
        provider_keys = [(item.provider, item.value) for item in self.provider_ids]
        if len(provider_keys) != len(set(provider_keys)):
            raise ValueError("paper provider identifiers must be unique")
        metric_providers = [item.provider for item in self.provider_metrics]
        if len(metric_providers) != len(set(metric_providers)):
            raise ValueError("paper provider metrics must be unique per provider")
        reference_keys = [(item.provider, item.value) for item in self.references]
        if len(reference_keys) != len(set(reference_keys)):
            raise ValueError("paper references must be unique")
        return self


class PaperIdentifier(DiscoveryRecord):
    """A provider-scoped identifier; IDs from different providers are not interchangeable."""

    provider: LiteratureProvider
    value: str = Field(min_length=1, max_length=500)


class ProviderMetrics(DiscoveryRecord):
    """Provider-specific counts; citation counts from different sources are not pooled."""

    provider: LiteratureProvider
    citation_count: int | None = Field(default=None, ge=0)
    reference_count: int | None = Field(default=None, ge=0)


PaperReference.model_rebuild()


class LiteratureSearchQuery(DiscoveryRecord):
    query: str = Field(min_length=2, max_length=500)
    year_from: int | None = Field(default=None, ge=1900, le=2200)
    year_to: int | None = Field(default=None, ge=1900, le=2200)
    venue: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_query_window(self) -> Self:
        if not self.query.strip():
            raise ValueError("literature query must not be blank")
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from must not exceed year_to")
        return self


class LiteratureSearchPage(DiscoveryRecord):
    provider: LiteratureProvider
    query: LiteratureSearchQuery
    papers: list[PaperReference] = Field(default_factory=list)
    total_count: int | None = Field(default=None, ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    source_url: str
    retrieved_at: AwareDatetime = Field(default_factory=utc_now)
    warnings: list[str] = Field(default_factory=list)


class ProviderSearchError(DiscoveryRecord):
    provider: LiteratureProvider
    message: str = Field(min_length=1)


class LiteratureSearchReport(DiscoveryRecord):
    query: LiteratureSearchQuery
    pages: list[LiteratureSearchPage] = Field(default_factory=list)
    papers: list[PaperReference] = Field(default_factory=list)
    provider_errors: list[ProviderSearchError] = Field(default_factory=list)
    retrieved_at: AwareDatetime = Field(default_factory=utc_now)


class CitationEdge(DiscoveryRecord):
    source_paper_id: str = Field(min_length=1)
    target_identifier: PaperIdentifier
    target_paper_id: str | None = None


class ResearchLandscape(DiscoveryRecord):
    topic_id: str = Field(min_length=1)
    query: LiteratureSearchQuery
    papers: list[PaperReference] = Field(default_factory=list)
    citation_edges: list[CitationEdge] = Field(default_factory=list)
    provider_counts: dict[str, int] = Field(default_factory=dict)
    year_counts: dict[int, int] = Field(default_factory=dict)
    unresolved_reference_count: int = Field(default=0, ge=0)
    provider_errors: list[ProviderSearchError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: AwareDatetime = Field(default_factory=utc_now)


class FullTextArtifact(DiscoveryRecord):
    paper_id: str = Field(min_length=1)
    source_url: str
    pdf_path: str
    pdf_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_bytes: int = Field(gt=0)
    text_path: str | None = None
    text_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    text_characters: int | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=0)
    retrieved_at: AwareDatetime = Field(default_factory=utc_now)


class StatementKind(StrEnum):
    CLAIM = "claim"
    ASSUMPTION = "assumption"
    LIMITATION = "limitation"


class ExtractedStatement(DiscoveryRecord):
    id: str = Field(min_length=1)
    kind: StatementKind
    statement: str = Field(min_length=8)
    evidence_locator: str = Field(min_length=3)
    supporting_quote: str = Field(min_length=8, max_length=1200)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class PaperAnalysis(DiscoveryRecord):
    """Provisional extraction; statements require source review before scientific use."""

    paper_id: str = Field(min_length=1)
    source_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extraction_model: str | None = None
    statements: list[ExtractedStatement] = Field(default_factory=list)
    reviewed_by_human: bool = False
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def unique_statement_ids(self) -> Self:
        identifiers = [statement.id for statement in self.statements]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("paper analysis statement IDs must be unique")
        return self


class ResearchGap(DiscoveryRecord):
    """A provisional gap grounded in prior work and explicit counterevidence."""

    id: str = Field(min_length=1)
    statement: str = Field(min_length=12)
    importance_rationale: str = Field(min_length=12)
    why_unresolved: str = Field(min_length=12)
    supporting_source_ids: list[str] = Field(min_length=1)
    counterevidence_source_ids: list[str] = Field(default_factory=list)


class IdeaCandidate(DiscoveryRecord):
    """A falsifiable candidate, not yet a validated contribution."""

    id: str = Field(min_length=1)
    gap_id: str = Field(min_length=1)
    title: str = Field(min_length=5)
    research_question: str = Field(min_length=12)
    hypothesis: str = Field(min_length=12)
    proposed_mechanism: str = Field(min_length=12)
    expected_contribution: str = Field(min_length=12)
    alternative_explanations: list[str] = Field(min_length=1)
    falsification_conditions: list[str] = Field(min_length=1)
    novelty_dimension: NoveltyDimension | None = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    counterevidence_evidence_ids: list[str] = Field(default_factory=list)


class NoveltyDifference(DiscoveryRecord):
    """One evidence-backed difference from a specific nearest-neighbor work."""

    dimension: NoveltyDimension
    nearest_work_id: str = Field(min_length=1)
    prior_art_claim: str = Field(min_length=12)
    proposed_difference: str = Field(min_length=12)
    materiality_rationale: str = Field(min_length=12)
    evidence_locator: str = Field(min_length=3)
    supporting_quote: str | None = Field(default=None, min_length=8, max_length=1200)


class NoveltyAudit(DiscoveryRecord):
    """Versionable prior-art review; a provisional judgment, never proof of novelty."""

    id: str = Field(min_length=1)
    idea_id: str = Field(min_length=1)
    verdict: AuditVerdict
    search_platforms: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    searched_paper_ids: list[str] = Field(default_factory=list)
    nearest_work_ids: list[str] = Field(default_factory=list)
    differences: list[NoveltyDifference] = Field(default_factory=list)
    overlap_risks: list[str] = Field(default_factory=list)
    blocking_overlap_questions: list[str] = Field(default_factory=list)
    reviewed_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_audit_evidence(self) -> Self:
        for name, values in (
            ("search_platforms", self.search_platforms),
            ("search_queries", self.search_queries),
            ("searched_paper_ids", self.searched_paper_ids),
            ("nearest_work_ids", self.nearest_work_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        if any(not value.strip() for value in self.search_platforms + self.search_queries):
            raise ValueError("search platforms and queries must not contain blank values")
        if self.verdict is AuditVerdict.PROVISIONALLY_DISTINCT:
            required = {
                "search_platforms": self.search_platforms,
                "search_queries": self.search_queries,
                "searched_paper_ids": self.searched_paper_ids,
                "nearest_work_ids": self.nearest_work_ids,
                "differences": self.differences,
            }
            missing = [name for name, values in required.items() if not values]
            if missing:
                raise ValueError(
                    "provisionally distinct audit requires recorded search coverage "
                    f"and nearest-work differences: {', '.join(missing)}"
                )
            difference_work_ids = {item.nearest_work_id for item in self.differences}
            if not difference_work_ids.issubset(set(self.nearest_work_ids)):
                raise ValueError("every novelty difference must reference a listed nearest work")
            if any(not item.supporting_quote for item in self.differences):
                raise ValueError("provisionally distinct novelty differences require source quotes")
        if self.verdict is AuditVerdict.OVERLAP_FOUND and not self.nearest_work_ids:
            raise ValueError("overlap_found audit must identify at least one nearest work")
        return self


class ExperimentDesign(DiscoveryRecord):
    """A minimal, discriminative experiment proposal; not an execution receipt."""

    id: str = Field(min_length=1)
    idea_id: str = Field(min_length=1)
    dataset_or_environment: str = Field(min_length=3)
    primary_metric: str = Field(min_length=2)
    metric_direction: MetricDirection
    baseline_refs: list[str] = Field(min_length=1)
    controls: list[str] = Field(min_length=1)
    protocol: str = Field(min_length=20)
    hypothesis_prediction: str = Field(min_length=8)
    alternative_predictions: list[str] = Field(min_length=1)
    falsification_rule: str = Field(min_length=12)
    held_out_conditions: list[str] = Field(min_length=1)
    estimated_cost: float = Field(gt=0, allow_inf_nan=False)
    cost_unit: str = Field(min_length=1)
    created_at: AwareDatetime = Field(default_factory=utc_now)


class DiscoveryWorkspace(DiscoveryRecord):
    """Small aggregate for provisional topic-discovery artifacts."""

    id: str = Field(min_length=1)
    topic: TopicSpec
    papers: list[PaperReference] = Field(default_factory=list)
    gaps: list[ResearchGap] = Field(default_factory=list)
    ideas: list[IdeaCandidate] = Field(default_factory=list)
    novelty_audits: list[NoveltyAudit] = Field(default_factory=list)
    experiment_designs: list[ExperimentDesign] = Field(default_factory=list)
    selected_idea_id: str | None = None

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        paper_ids = [paper.id for paper in self.papers]
        gap_ids = [gap.id for gap in self.gaps]
        idea_ids = [idea.id for idea in self.ideas]
        audit_ids = [audit.id for audit in self.novelty_audits]
        design_ids = [design.id for design in self.experiment_designs]

        for name, values in (
            ("paper", paper_ids),
            ("gap", gap_ids),
            ("idea", idea_ids),
            ("novelty audit", audit_ids),
            ("experiment design", design_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} IDs must be unique")

        known_papers = set(paper_ids)
        known_gaps = set(gap_ids)
        known_ideas = set(idea_ids)

        for gap in self.gaps:
            referenced = set(gap.supporting_source_ids + gap.counterevidence_source_ids)
            missing = referenced - known_papers
            if missing:
                raise ValueError(f"gap {gap.id} references unknown papers: {sorted(missing)}")

        for idea in self.ideas:
            if idea.gap_id not in known_gaps:
                raise ValueError(f"idea {idea.id} references unknown gap: {idea.gap_id}")

        for audit in self.novelty_audits:
            if audit.idea_id not in known_ideas:
                raise ValueError(f"audit {audit.id} references unknown idea: {audit.idea_id}")
            referenced = (
                set(audit.searched_paper_ids)
                | set(audit.nearest_work_ids)
                | {difference.nearest_work_id for difference in audit.differences}
            )
            missing = referenced - known_papers
            if missing:
                raise ValueError(f"audit {audit.id} references unknown papers: {sorted(missing)}")

        for design in self.experiment_designs:
            if design.idea_id not in known_ideas:
                raise ValueError(
                    f"experiment design {design.id} references unknown idea: {design.idea_id}"
                )

        if self.selected_idea_id is not None and self.selected_idea_id not in known_ideas:
            raise ValueError(f"selected idea does not exist: {self.selected_idea_id}")
        return self


class ExperimentReadiness(DiscoveryRecord):
    """Derived gate report; passing it does not authorize or prove an experiment."""

    ready_for_human_review: bool
    selected_idea_id: str | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    qualification: str = (
        "This is a completeness and budget check only. It does not prove novelty, "
        "statistical power, hypothesis truth, or experiment validity."
    )
