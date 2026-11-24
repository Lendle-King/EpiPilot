"""Typed V0.2 contracts for evidence synthesis, idea diversity and prior-art challenge."""

from __future__ import annotations

import re
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from epipilot.core.models import utc_now
from epipilot.topic_discovery.models import (
    AuditVerdict,
    DiscoveryRecord,
    IdeaCandidate,
    NoveltyDimension,
    PaperReference,
    ResearchGap,
    StatementKind,
)


class EvidenceStatementRef(DiscoveryRecord):
    """Stable pointer to one quote-verified statement from one paper."""

    id: str = Field(min_length=3)
    paper_id: str = Field(min_length=1)
    statement_id: str = Field(min_length=1)
    kind: StatementKind
    evidence_locator: str = Field(min_length=3)
    supporting_quote: str = Field(min_length=8, max_length=1200)

    @model_validator(mode="after")
    def validate_stable_id(self) -> Self:
        if self.id != f"{self.paper_id}::{self.statement_id}":
            raise ValueError("evidence ID must be the paper-qualified statement ID")
        return self


class ResearchTheme(DiscoveryRecord):
    """A synthesis theme supported by one or more source-grounded statements."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=3)
    summary: str = Field(min_length=20)
    evidence_ids: list[str] = Field(min_length=1)
    unresolved_tensions: list[str] = Field(default_factory=list)


class SynthesisGap(ResearchGap):
    """A gap hypothesis with statement-level support and counterevidence."""

    supporting_evidence_ids: list[str] = Field(min_length=1)
    counterevidence_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_references(self) -> Self:
        for name, values in (
            ("supporting_evidence_ids", self.supporting_evidence_ids),
            ("counterevidence_evidence_ids", self.counterevidence_evidence_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        if set(self.supporting_evidence_ids) & set(self.counterevidence_evidence_ids):
            raise ValueError("supporting and counterevidence references must be disjoint")
        return self


class ResearchSynthesis(DiscoveryRecord):
    """A source-pinned synthesis; gap statements remain provisional hypotheses."""

    id: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)
    source_paper_ids: list[str] = Field(min_length=1)
    source_text_sha256: dict[str, str]
    evidence_index: list[EvidenceStatementRef] = Field(min_length=1)
    themes: list[ResearchTheme] = Field(default_factory=list)
    gaps: list[SynthesisGap] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    coverage_limitations: list[str] = Field(default_factory=list)
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_synthesis_references(self) -> Self:
        if len(self.source_paper_ids) != len(set(self.source_paper_ids)):
            raise ValueError("source_paper_ids must be unique")
        if set(self.source_text_sha256) != set(self.source_paper_ids):
            raise ValueError("source text hashes must cover exactly the source paper IDs")
        if any(
            re.fullmatch(r"[a-f0-9]{64}", digest) is None
            for digest in self.source_text_sha256.values()
        ):
            raise ValueError("source text hashes must be lowercase SHA-256 digests")

        evidence_ids = [item.id for item in self.evidence_index]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence index IDs must be unique")
        known_papers = set(self.source_paper_ids)
        if any(item.paper_id not in known_papers for item in self.evidence_index):
            raise ValueError("evidence index references a paper outside the synthesis sources")
        known_evidence = set(evidence_ids)

        theme_ids = [theme.id for theme in self.themes]
        gap_ids = [gap.id for gap in self.gaps]
        if len(theme_ids) != len(set(theme_ids)):
            raise ValueError("research theme IDs must be unique")
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("synthesis gap IDs must be unique")

        for theme in self.themes:
            if not set(theme.evidence_ids).issubset(known_evidence):
                raise ValueError(f"theme {theme.id} references unknown evidence")
        evidence_by_id = {item.id: item for item in self.evidence_index}
        for gap in self.gaps:
            supporting = set(gap.supporting_evidence_ids)
            counter = set(gap.counterevidence_evidence_ids)
            if not supporting.issubset(known_evidence) or not counter.issubset(known_evidence):
                raise ValueError(f"gap {gap.id} references unknown evidence")
            support_sources = {evidence_by_id[item].paper_id for item in supporting}
            counter_sources = {evidence_by_id[item].paper_id for item in counter}
            if support_sources != set(gap.supporting_source_ids):
                raise ValueError(f"gap {gap.id} supporting source IDs do not match its evidence")
            if counter_sources != set(gap.counterevidence_source_ids):
                raise ValueError(
                    f"gap {gap.id} counterevidence source IDs do not match its evidence"
                )
        return self


class IdeaCandidateV2(IdeaCandidate):
    """An idea candidate with explicit novelty axis and evidence lineage."""

    novelty_dimension: NoveltyDimension | None = Field(...)
    supporting_evidence_ids: list[str] = Field(min_length=1)
    counterevidence_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> Self:
        if self.novelty_dimension is None:
            raise ValueError("V0.2 idea candidates require a novelty dimension")
        for name, values in (
            ("supporting_evidence_ids", self.supporting_evidence_ids),
            ("counterevidence_evidence_ids", self.counterevidence_evidence_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        if set(self.supporting_evidence_ids) & set(self.counterevidence_evidence_ids):
            raise ValueError("idea support and counterevidence references must be disjoint")
        return self


class IdeaBatch(DiscoveryRecord):
    """A deliberately diverse batch of 5–8 falsifiable research candidates."""

    id: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)
    synthesis_id: str = Field(min_length=1)
    target_count: int = Field(ge=5, le=8)
    candidates: list[IdeaCandidateV2] = Field(min_length=5, max_length=8)
    generation_notes: list[str] = Field(default_factory=list)
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_candidate_batch(self) -> Self:
        if len(self.candidates) > self.target_count:
            raise ValueError("candidate count must not exceed target_count")
        identifiers = [candidate.id for candidate in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("idea candidate IDs must be unique")
        dimensions = {candidate.novelty_dimension for candidate in self.candidates}
        if len(dimensions) < 3:
            raise ValueError("idea batch must cover at least three novelty dimensions")
        return self


class PriorArtSearchFailure(DiscoveryRecord):
    provider: str = Field(min_length=1)
    query: str = Field(min_length=2)
    message: str = Field(min_length=1)


class PriorArtMatch(DiscoveryRecord):
    """Lexical retrieval result; the score is not a semantic novelty judgment."""

    rank: int = Field(ge=1)
    paper_id: str = Field(min_length=1)
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    title_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    abstract_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    keyword_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    matched_terms: list[str] = Field(default_factory=list)
    matched_fields: list[str] = Field(default_factory=list)
    abstract_available: bool = False


class PriorArtChallengeReport(DiscoveryRecord):
    """Search coverage and ranked nearest-neighbor candidates for one research idea."""

    id: str = Field(min_length=1)
    idea_id: str = Field(min_length=1)
    search_queries: list[str] = Field(min_length=1)
    providers_attempted: list[str] = Field(min_length=1)
    providers_succeeded: list[str] = Field(default_factory=list)
    failures: list[PriorArtSearchFailure] = Field(default_factory=list)
    papers: list[PaperReference] = Field(default_factory=list)
    nearest_matches: list[PriorArtMatch] = Field(default_factory=list)
    ranking_method: str = "weighted_field_coverage_v1"
    warnings: list[str] = Field(default_factory=list)
    retrieved_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_challenge_references(self) -> Self:
        for name, values in (
            ("search_queries", self.search_queries),
            ("providers_attempted", self.providers_attempted),
            ("providers_succeeded", self.providers_succeeded),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        if any(not value.strip() for value in self.search_queries):
            raise ValueError("search queries must not contain blank values")
        if not set(self.providers_succeeded).issubset(set(self.providers_attempted)):
            raise ValueError("successful providers must be included in providers_attempted")
        if any(failure.provider not in self.providers_attempted for failure in self.failures):
            raise ValueError("provider failures must reference an attempted provider")
        paper_ids = [paper.id for paper in self.papers]
        if len(paper_ids) != len(set(paper_ids)):
            raise ValueError("prior-art paper IDs must be unique")
        if not {match.paper_id for match in self.nearest_matches}.issubset(set(paper_ids)):
            raise ValueError("nearest matches must reference papers in the challenge report")
        ranks = [match.rank for match in self.nearest_matches]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("nearest match ranks must be consecutive and ordered")
        return self


class PriorArtComparison(DiscoveryRecord):
    """An adversarial, source-quoted comparison against one nearest-neighbor paper."""

    nearest_work_id: str = Field(min_length=1)
    dimension: NoveltyDimension
    prior_art_claim: str = Field(min_length=12)
    proposed_difference: str = Field(min_length=12)
    materiality_rationale: str = Field(min_length=12)
    evidence_locator: str = Field(min_length=3)
    supporting_quote: str = Field(min_length=8, max_length=1200)


class PriorArtReview(DiscoveryRecord):
    """Machine-drafted review; it cannot mark itself as human-approved."""

    challenge_id: str = Field(min_length=1)
    idea_id: str = Field(min_length=1)
    verdict: AuditVerdict
    providers_succeeded: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    searched_paper_ids: list[str] = Field(default_factory=list)
    nearest_work_ids: list[str] = Field(default_factory=list)
    comparisons: list[PriorArtComparison] = Field(default_factory=list)
    overlap_risks: list[str] = Field(default_factory=list)
    blocking_overlap_questions: list[str] = Field(default_factory=list)
    reviewer_notes: list[str] = Field(default_factory=list)
    reviewed_by_human: bool = False
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_review_coverage(self) -> Self:
        for name, values in (
            ("providers_succeeded", self.providers_succeeded),
            ("search_queries", self.search_queries),
            ("searched_paper_ids", self.searched_paper_ids),
            ("nearest_work_ids", self.nearest_work_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        if any(not value.strip() for value in self.search_queries):
            raise ValueError("search queries must not contain blank values")
        if not set(self.nearest_work_ids).issubset(set(self.searched_paper_ids)):
            raise ValueError("nearest works must be included in searched_paper_ids")
        comparison_ids = [item.nearest_work_id for item in self.comparisons]
        if len(comparison_ids) != len(set(comparison_ids)):
            raise ValueError("prior-art comparisons must be unique per nearest work")
        if not set(comparison_ids).issubset(set(self.nearest_work_ids)):
            raise ValueError("comparisons must reference listed nearest works")
        if self.verdict is AuditVerdict.PROVISIONALLY_DISTINCT:
            if len(self.providers_succeeded) < 2 or len(self.searched_paper_ids) < 5:
                raise ValueError("provisionally distinct review requires broader search coverage")
            if not self.nearest_work_ids:
                raise ValueError("provisionally distinct review requires nearest-work comparisons")
            if set(comparison_ids) != set(self.nearest_work_ids):
                raise ValueError("every nearest work requires an evidence-backed comparison")
        if self.verdict is AuditVerdict.OVERLAP_FOUND:
            if not self.nearest_work_ids or not self.comparisons:
                raise ValueError("overlap_found review requires a cited overlapping work")
        return self
