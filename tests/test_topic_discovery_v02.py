"""Offline tests for V0.2 synthesis, diverse idea generation and prior-art challenge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from epipilot.topic_discovery.idea_generation import (
    build_idea_generation_prompt,
    validate_idea_batch,
)
from epipilot.topic_discovery.models import (
    AuditVerdict,
    DiscoveryWorkspace,
    ExtractedStatement,
    IdeaCandidate,
    LiteratureProvider,
    LiteratureSearchPage,
    LiteratureSearchQuery,
    NoveltyDimension,
    PaperAnalysis,
    PaperIdentifier,
    PaperReference,
    PaperSourceKind,
    ResearchGap,
    StatementKind,
    TopicSpec,
)
from epipilot.topic_discovery.prior_art import (
    PriorArtChallengeService,
    attach_prior_art_review,
    build_prior_art_challenge_prompt,
    confirm_prior_art_review,
    rank_prior_art,
    validate_prior_art_review,
)
from epipilot.topic_discovery.synthesis import (
    build_evidence_index,
    build_research_synthesis_prompt,
    validate_research_synthesis,
)
from epipilot.topic_discovery.v02_models import (
    IdeaBatch,
    IdeaCandidateV2,
    PriorArtChallengeReport,
    PriorArtComparison,
    PriorArtReview,
    ResearchSynthesis,
    ResearchTheme,
    SynthesisGap,
)
from epipilot.topic_discovery.cli import main


def _topic() -> TopicSpec:
    return TopicSpec(
        id="topic-embodied-skills",
        title="Reliable embodied skill composition",
        description="Study physical competence and reliable composition of frozen robot skills.",
        research_area="Embodied AI",
        target_venues=["ICML", "ICLR"],
    )


def _papers() -> list[PaperReference]:
    abstracts = [
        (
            "Contact-Aware Robot Skill Transfer",
            "We study contact-conditioned policies for manipulation. "
            "The method predicts success from contact state and object pose.",
        ),
        (
            "Learning Robust Skill Initiation Sets",
            "We learn initiation sets for reusable robot options. "
            "The method evaluates success under varied object configurations.",
        ),
        (
            "Temporal Abstraction for Long-Horizon Tasks",
            "We study temporal abstraction under delayed feedback. "
            "The policy selects reusable skills over long task sequences.",
        ),
        (
            "Physical Reasoning for Manipulation",
            "We use object geometry and contact constraints for manipulation planning. "
            "The system replans when physical constraints change.",
        ),
        (
            "Failure Recovery in Robot Policies",
            "We evaluate recovery policies after failed manipulation attempts. "
            "The controller detects failure and retries an action sequence.",
        ),
        (
            "Compositional Generalization of Robot Skills",
            "We study unseen compositions of learned robot skills. "
            "The evaluation measures transfer to held-out task combinations.",
        ),
    ]
    return [
        PaperReference(
            id=f"paper-{index}",
            title=title,
            year=2024 + index % 2,
            doi=f"10.5555/embodied.{index}",
            url=f"https://example.org/papers/{index}",
            abstract=abstract,
            relevance_note="A synthetic but traceable paper fixture for offline testing.",
            source_kind=PaperSourceKind.PAPER,
            keywords=["robotics", "skill composition"],
        )
        for index, (title, abstract) in enumerate(abstracts, start=1)
    ]


def _analyses() -> list[PaperAnalysis]:
    text_one = (
        "We assume the contact state is observable. "
        "Our method predicts frozen-skill success under held-out object poses."
    )
    text_two = (
        "The evaluation does not isolate delayed feedback from contact uncertainty. "
        "We report failures on unseen skill compositions."
    )
    return [
        PaperAnalysis(
            paper_id="paper-1",
            source_text_sha256=hashlib.sha256(text_one.encode()).hexdigest(),
            extraction_model="test-model",
            statements=[
                ExtractedStatement(
                    id="assumption-1",
                    kind=StatementKind.ASSUMPTION,
                    statement="The contact state is assumed to be observable.",
                    evidence_locator="Section 3",
                    supporting_quote="We assume the contact state is observable.",
                ),
                ExtractedStatement(
                    id="claim-1",
                    kind=StatementKind.CLAIM,
                    statement=(
                        "The method predicts frozen-skill success under held-out object poses."
                    ),
                    evidence_locator="Section 4",
                    supporting_quote=(
                        "Our method predicts frozen-skill success under held-out object poses."
                    ),
                ),
            ],
        ),
        PaperAnalysis(
            paper_id="paper-2",
            source_text_sha256=hashlib.sha256(text_two.encode()).hexdigest(),
            extraction_model="test-model",
            statements=[
                ExtractedStatement(
                    id="limitation-1",
                    kind=StatementKind.LIMITATION,
                    statement=(
                        "The evaluation does not isolate delayed feedback from contact uncertainty."
                    ),
                    evidence_locator="Limitations",
                    supporting_quote=(
                        "The evaluation does not isolate delayed feedback from contact uncertainty."
                    ),
                ),
                ExtractedStatement(
                    id="claim-2",
                    kind=StatementKind.CLAIM,
                    statement="The paper reports failures on unseen skill compositions.",
                    evidence_locator="Section 5",
                    supporting_quote="We report failures on unseen skill compositions.",
                ),
            ],
        ),
    ]


def _synthesis() -> ResearchSynthesis:
    papers = _papers()
    analyses = _analyses()
    evidence = build_evidence_index(papers, analyses)
    evidence_by_id = {item.id: item for item in evidence}
    support_id = "paper-1::claim-1"
    counter_id = "paper-2::limitation-1"
    return ResearchSynthesis(
        id="synthesis-test-1",
        topic_id=_topic().id,
        source_paper_ids=["paper-1", "paper-2"],
        source_text_sha256={
            analysis.paper_id: analysis.source_text_sha256 for analysis in analyses
        },
        evidence_index=evidence,
        themes=[
            ResearchTheme(
                id="theme-1",
                label="Physical competence prediction",
                summary="Prior work predicts skill success using contact and object state.",
                evidence_ids=[support_id],
                unresolved_tensions=["Delayed feedback is not isolated from contact uncertainty."],
            )
        ],
        gaps=[
            SynthesisGap(
                id="gap-1",
                statement=(
                    "The interaction between delayed feedback and contact uncertainty "
                    "in frozen-skill competence remains under-characterized."
                ),
                importance_rationale=(
                    "Separating these effects may explain brittle long-horizon skill execution."
                ),
                why_unresolved=(
                    "The supplied studies evaluate these factors separately or confound them."
                ),
                supporting_source_ids=["paper-2"],
                counterevidence_source_ids=["paper-1"],
                supporting_evidence_ids=[counter_id],
                counterevidence_evidence_ids=[support_id],
            )
        ],
        unresolved_questions=["Can delayed observations change the physical competence boundary?"],
        coverage_limitations=["Only two analyzed papers are represented in this test fixture."],
    )


def _idea_batch(synthesis: ResearchSynthesis) -> IdeaBatch:
    evidence_support = synthesis.gaps[0].supporting_evidence_ids
    evidence_counter = synthesis.gaps[0].counterevidence_evidence_ids
    candidates = [
        IdeaCandidateV2(
            id="idea-1",
            gap_id="gap-1",
            title="Causal contact-delay competence certificates",
            research_question="Can contact-delay interactions predict frozen-skill failure?",
            hypothesis="A causal certificate improves prediction on unseen contact-delay pairs.",
            proposed_mechanism=(
                "A state-conditioned certificate isolates coupled contact and delay effects."
            ),
            expected_contribution=(
                "A transferable competence certificate for delayed physical interaction."
            ),
            alternative_explanations=["The gain is caused only by additional state features."],
            falsification_conditions=["A matched critic achieves the same held-out calibration."],
            novelty_dimension=NoveltyDimension.MECHANISM,
            supporting_evidence_ids=evidence_support,
            counterevidence_evidence_ids=evidence_counter,
        ),
        IdeaCandidateV2(
            id="idea-2",
            gap_id="gap-1",
            title="Intervention-aware skill boundary estimation",
            research_question=(
                "Can interventions identify state variables that govern skill failure?"
            ),
            hypothesis="Targeted interventions improve boundary identification under confounding.",
            proposed_mechanism=(
                "Counterfactual contact perturbations isolate causal competence factors."
            ),
            expected_contribution=(
                "An intervention protocol for identifying skill-specific failure boundaries."
            ),
            alternative_explanations=["More diverse data alone explains the improvement."],
            falsification_conditions=["Passive matched data reaches the same boundary accuracy."],
            novelty_dimension=NoveltyDimension.METHOD,
            supporting_evidence_ids=evidence_support,
            counterevidence_evidence_ids=evidence_counter,
        ),
        IdeaCandidateV2(
            id="idea-3",
            gap_id="gap-1",
            title="Delayed-observation safe skill scheduling",
            research_question="How should a scheduler react to stale physical observations?",
            hypothesis="Delay-aware scheduling reduces unsafe frozen-skill initiation.",
            proposed_mechanism=(
                "The scheduler conditions skill selection on observation age and contact mode."
            ),
            expected_contribution="A delay-aware scheduling rule for reliable skill composition.",
            alternative_explanations=["A shorter fixed observation interval is sufficient."],
            falsification_conditions=["A fixed-rate scheduler has equal success at equal cost."],
            novelty_dimension=NoveltyDimension.PROBLEM,
            supporting_evidence_ids=evidence_support,
            counterevidence_evidence_ids=evidence_counter,
        ),
        IdeaCandidateV2(
            id="idea-4",
            gap_id="gap-1",
            title="Cross-object contact transition invariants",
            research_question="Which contact invariants transfer across unseen object geometries?",
            hypothesis=(
                "Invariant contact transitions predict skill transfer across object families."
            ),
            proposed_mechanism=(
                "A relational representation tracks contact topology across object instances."
            ),
            expected_contribution="A cross-object invariant for composing manipulation skills.",
            alternative_explanations=["Object-family labels explain the apparent transfer."],
            falsification_conditions=["A label-matched baseline transfers equally well."],
            novelty_dimension=NoveltyDimension.INSIGHT,
            supporting_evidence_ids=evidence_support,
            counterevidence_evidence_ids=evidence_counter,
        ),
        IdeaCandidateV2(
            id="idea-5",
            gap_id="gap-1",
            title="Adaptive observation contracts for contact-rich tasks",
            research_question=(
                "When should a robot request a fresh observation before skill execution?"
            ),
            hypothesis="Adaptive observation contracts reduce delay-induced execution errors.",
            proposed_mechanism=(
                "A risk trigger requests observations when contact uncertainty exceeds a bound."
            ),
            expected_contribution=(
                "A verifiable observation contract for long-horizon manipulation."
            ),
            alternative_explanations=["A more frequent fixed observation schedule is enough."],
            falsification_conditions=[
                "Fixed-rate sensing matches the adaptive policy at equal cost."
            ],
            novelty_dimension=NoveltyDimension.SCENARIO,
            supporting_evidence_ids=evidence_support,
            counterevidence_evidence_ids=evidence_counter,
        ),
    ]
    return IdeaBatch(
        id="ideas-test-1",
        topic_id=synthesis.topic_id,
        synthesis_id=synthesis.id,
        target_count=6,
        candidates=candidates,
    )


class FakeProvider:
    def __init__(
        self,
        provider: LiteratureProvider,
        papers: list[PaperReference],
    ) -> None:
        self.provider = provider
        self.papers = papers

    def search(self, query: LiteratureSearchQuery) -> LiteratureSearchPage:
        provider_papers = [
            paper.model_copy(
                update={
                    "provider_ids": [
                        PaperIdentifier(
                            provider=self.provider,
                            value=f"{self.provider.value}:{paper.id}",
                        )
                    ]
                }
            )
            for paper in self.papers
        ]
        return LiteratureSearchPage(
            provider=self.provider,
            query=query,
            papers=provider_papers,
            total_count=len(provider_papers),
            source_url=f"https://example.org/{self.provider.value}/search",
        )


class FailingProvider:
    provider = LiteratureProvider.ARXIV

    def search(self, query: LiteratureSearchQuery) -> LiteratureSearchPage:
        raise RuntimeError("simulated provider outage")


class PagedProvider:
    provider = LiteratureProvider.OPENALEX

    def __init__(self, papers: list[PaperReference]) -> None:
        self.papers = papers
        self.offsets: list[int] = []

    def search(self, query: LiteratureSearchQuery) -> LiteratureSearchPage:
        self.offsets.append(query.offset)
        start = query.offset
        end = start + query.limit
        page_papers = [
            paper.model_copy(
                update={
                    "provider_ids": [
                        PaperIdentifier(
                            provider=self.provider,
                            value=f"{self.provider.value}:{paper.id}",
                        )
                    ]
                }
            )
            for paper in self.papers[start:end]
        ]
        next_offset = end if end < len(self.papers) else None
        return LiteratureSearchPage(
            provider=self.provider,
            query=query,
            papers=page_papers,
            total_count=len(self.papers),
            next_offset=next_offset,
            source_url="https://example.org/openalex/paged",
        )


def _idea() -> IdeaCandidate:
    return IdeaCandidate(
        id="idea-prior-art",
        gap_id="gap-1",
        title="Causal contact-delay competence certificates",
        research_question="Can contact-delay interactions predict frozen-skill failure?",
        hypothesis="A causal certificate improves prediction on unseen contact-delay pairs.",
        proposed_mechanism=(
            "A state-conditioned certificate isolates coupled contact and delay effects."
        ),
        expected_contribution=(
            "A transferable competence certificate for delayed physical interaction."
        ),
        alternative_explanations=["The gain is caused only by additional state features."],
        falsification_conditions=["A matched critic achieves the same held-out calibration."],
    )


def test_evidence_index_uses_paper_qualified_statement_ids() -> None:
    evidence = build_evidence_index(_papers(), _analyses())
    identifiers = {item.id for item in evidence}
    assert "paper-1::claim-1" in identifiers
    assert "paper-2::limitation-1" in identifiers
    assert len(identifiers) == 4


def test_synthesis_prompt_preserves_quotes_and_source_hashes() -> None:
    prompt = build_research_synthesis_prompt(_topic(), _papers(), _analyses())
    assert "paper-1::claim-1" in prompt
    assert "Our method predicts frozen-skill success" in prompt
    assert "source_text_sha256" in prompt
    assert "不是“文献没有做过”的断言" in prompt


def test_synthesis_validator_checks_source_hashes_and_evidence_index() -> None:
    synthesis = _synthesis()
    validated = validate_research_synthesis(
        _topic(),
        _papers(),
        _analyses(),
        synthesis.model_dump_json(),
    )
    assert validated.id == synthesis.id

    payload = synthesis.model_dump()
    payload["evidence_index"][0]["supporting_quote"] = "A fabricated source quote."
    with pytest.raises(ValueError, match="changed or omitted the immutable evidence index"):
        validate_research_synthesis(_topic(), _papers(), _analyses(), json.dumps(payload))


def test_synthesis_validator_injects_immutable_evidence_index() -> None:
    synthesis = _synthesis()
    payload = synthesis.model_dump()
    payload.pop("evidence_index")
    validated = validate_research_synthesis(
        _topic(),
        _papers(),
        _analyses(),
        json.dumps(payload),
    )
    assert len(validated.evidence_index) == 4
    assert "paper-1::claim-1" in {item.id for item in validated.evidence_index}


def test_synthesis_validator_rejects_a_different_topic() -> None:
    synthesis = _synthesis().model_copy(update={"topic_id": "other-topic"})
    with pytest.raises(ValueError, match="different topic"):
        validate_research_synthesis(
            _topic(),
            _papers(),
            _analyses(),
            synthesis.model_dump_json(),
        )


def test_idea_prompt_requires_mechanism_diversity_and_falsification() -> None:
    synthesis = _synthesis()
    prompt = build_idea_generation_prompt(_topic(), synthesis, target_count=6)
    assert "novelty_dimension" in prompt
    assert "falsification_conditions" in prompt
    assert "supporting_evidence_ids" in prompt
    assert "至少覆盖三种" in prompt


def test_idea_batch_validates_evidence_and_diversity() -> None:
    synthesis = _synthesis()
    batch = _idea_batch(synthesis)
    validated = validate_idea_batch(
        _topic(),
        synthesis,
        batch.model_dump_json(),
    )
    assert len(validated.candidates) == 5
    assert len({candidate.novelty_dimension for candidate in validated.candidates}) == 5


def test_idea_batch_rejects_near_duplicate_candidates() -> None:
    synthesis = _synthesis()
    batch = _idea_batch(synthesis)
    candidates = list(batch.candidates)
    candidates[1] = candidates[1].model_copy(
        update={
            "title": candidates[0].title,
            "proposed_mechanism": candidates[0].proposed_mechanism,
        }
    )
    duplicate_batch = batch.model_copy(update={"candidates": candidates})
    with pytest.raises(ValueError, match="near-duplicate ideas detected"):
        validate_idea_batch(_topic(), synthesis, duplicate_batch.model_dump_json())


def test_idea_batch_rejects_evidence_from_an_unrelated_gap() -> None:
    synthesis = _synthesis()
    batch = _idea_batch(synthesis)
    candidates = list(batch.candidates)
    candidates[0] = candidates[0].model_copy(
        update={"supporting_evidence_ids": ["unknown-paper::claim"]}
    )
    invalid_batch = batch.model_copy(update={"candidates": candidates})
    with pytest.raises(ValueError, match="outside its gap's supporting evidence"):
        validate_idea_batch(_topic(), synthesis, invalid_batch.model_dump_json())


def test_prior_art_ranking_is_deterministic_and_field_transparent() -> None:
    idea = _idea()
    matches = rank_prior_art(idea, _papers(), top_k=4)
    assert len(matches) == 4
    assert matches[0].score >= matches[-1].score
    assert "title" in matches[0].matched_fields
    assert matches[0].matched_terms


def test_prior_art_service_searches_multiple_queries_and_providers() -> None:
    papers = _papers()
    service = PriorArtChallengeService(
        [
            FakeProvider(LiteratureProvider.OPENALEX, papers),
            FakeProvider(LiteratureProvider.SEMANTIC_SCHOLAR, papers),
        ],
        limit_per_query=10,
        top_k=5,
    )
    report = service.challenge(_idea(), year_from=2022, year_to=2026)
    assert len(report.search_queries) == 4
    assert set(report.providers_succeeded) == {
        LiteratureProvider.OPENALEX.value,
        LiteratureProvider.SEMANTIC_SCHOLAR.value,
    }
    assert len(report.papers) == 6
    assert len(report.nearest_matches) == 5
    assert report.ranking_method == "weighted_field_coverage_v1"


def test_prior_art_service_preserves_partial_provider_failures() -> None:
    service = PriorArtChallengeService(
        [
            FakeProvider(LiteratureProvider.OPENALEX, _papers()),
            FailingProvider(),
        ],
        max_queries=1,
        top_k=5,
    )
    report = service.challenge(_idea())
    assert report.providers_succeeded == [LiteratureProvider.OPENALEX.value]
    assert len(report.failures) == 1
    assert report.failures[0].provider == LiteratureProvider.ARXIV.value
    assert "simulated provider outage" in report.failures[0].message


def test_prior_art_service_follows_provider_pagination() -> None:
    provider = PagedProvider(_papers())
    service = PriorArtChallengeService(
        [provider],
        max_queries=1,
        limit_per_query=3,
        top_k=5,
        max_pages_per_provider_query=2,
    )
    report = service.challenge(_idea())
    assert provider.offsets == [0, 3]
    assert len(report.papers) == 6
    assert len(report.nearest_matches) == 5


def test_prior_art_prompt_exposes_retrieval_limits_and_adversarial_questions() -> None:
    service = PriorArtChallengeService(
        [FakeProvider(LiteratureProvider.OPENALEX, _papers())],
        top_k=3,
    )
    report = service.challenge(_idea())
    prompt = build_prior_art_challenge_prompt(_idea(), report)
    assert "主动推翻" in prompt
    assert "retrieval_score" in prompt
    assert report.id in prompt
    assert "paper-" in prompt


def _prior_art_review(report: PriorArtChallengeReport) -> PriorArtReview:
    papers = {paper.id: paper for paper in report.papers}
    selected = report.nearest_matches[:2]
    comparisons = [
        PriorArtComparison(
            nearest_work_id=match.paper_id,
            dimension=NoveltyDimension.MECHANISM,
            prior_art_claim="The prior work models contact state for manipulation.",
            proposed_difference="The candidate models coupled contact and observation delay.",
            materiality_rationale=(
                "The candidate tests a causal interaction absent from this prior claim."
            ),
            evidence_locator="Abstract",
            supporting_quote=papers[match.paper_id].abstract.split(". ")[0] + ".",
        )
        for match in selected
    ]
    return PriorArtReview(
        challenge_id=report.id,
        idea_id=report.idea_id,
        verdict=AuditVerdict.PROVISIONALLY_DISTINCT,
        providers_succeeded=report.providers_succeeded,
        search_queries=report.search_queries,
        searched_paper_ids=[paper.id for paper in report.papers],
        nearest_work_ids=[match.paper_id for match in selected],
        comparisons=comparisons,
        overlap_risks=["The distinction from robust option initiation sets needs deeper review."],
        blocking_overlap_questions=[],
        reviewer_notes=["This is only a provisional judgment within the retrieved corpus."],
    )


def test_prior_art_review_requires_verbatim_quotes_and_explicit_human_approval() -> None:
    idea = _idea()
    service = PriorArtChallengeService(
        [
            FakeProvider(LiteratureProvider.OPENALEX, _papers()),
            FakeProvider(LiteratureProvider.SEMANTIC_SCHOLAR, _papers()),
        ],
        top_k=5,
    )
    report = service.challenge(idea)
    review = _prior_art_review(report)
    validated = validate_prior_art_review(idea, report, review.model_dump_json())
    with pytest.raises(ValueError, match="human approval is required"):
        confirm_prior_art_review(validated, human_approved=False)
    audit = confirm_prior_art_review(validated, human_approved=True)
    assert audit.verdict is AuditVerdict.PROVISIONALLY_DISTINCT
    assert all(item.supporting_quote for item in audit.differences)


def test_human_approved_prior_art_attaches_to_existing_workspace() -> None:
    idea = _idea()
    service = PriorArtChallengeService(
        [
            FakeProvider(LiteratureProvider.OPENALEX, _papers()),
            FakeProvider(LiteratureProvider.SEMANTIC_SCHOLAR, _papers()),
        ],
        top_k=5,
    )
    report = service.challenge(idea)
    review = validate_prior_art_review(
        idea,
        report,
        _prior_art_review(report).model_dump_json(),
    )
    workspace = DiscoveryWorkspace(
        id="workspace-prior-art",
        topic=_topic(),
        papers=[report.papers[0]],
        gaps=[
            ResearchGap(
                id="gap-1",
                statement="The relationship between contact and delay is under-characterized.",
                importance_rationale="It may explain failure during long-horizon skill execution.",
                why_unresolved="The supplied studies do not isolate their interaction.",
                supporting_source_ids=[report.papers[0].id],
            )
        ],
        ideas=[idea],
        selected_idea_id=idea.id,
    )
    updated = attach_prior_art_review(
        workspace,
        report,
        review,
        human_approved=True,
    )
    assert len(updated.papers) == 6
    assert len(updated.novelty_audits) == 1
    assert updated.novelty_audits[0].idea_id == idea.id


def test_prior_art_review_rejects_hallucinated_quote() -> None:
    idea = _idea()
    service = PriorArtChallengeService(
        [
            FakeProvider(LiteratureProvider.OPENALEX, _papers()),
            FakeProvider(LiteratureProvider.SEMANTIC_SCHOLAR, _papers()),
        ],
        top_k=5,
    )
    report = service.challenge(idea)
    review = _prior_art_review(report)
    comparisons = list(review.comparisons)
    comparisons[0] = comparisons[0].model_copy(
        update={"supporting_quote": "This sentence is not in the source abstract."}
    )
    invalid_review = review.model_copy(update={"comparisons": comparisons})
    with pytest.raises(ValueError, match="not present in supplied source text"):
        validate_prior_art_review(idea, report, invalid_review.model_dump_json())


def test_cli_challenge_writes_report_and_adversarial_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idea = _idea()
    (tmp_path / "idea.json").write_text(idea.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        "epipilot.topic_discovery.cli.build_default_providers",
        lambda: [FakeProvider(LiteratureProvider.OPENALEX, _papers())],
    )
    result = main(
        [
            "--root",
            str(tmp_path),
            "challenge",
            "--idea",
            "idea.json",
            "--limit",
            "10",
            "--top-k",
            "5",
            "--output",
            "challenge.json",
            "--prompt-output",
            "challenge_prompt.md",
        ]
    )
    assert result == 0
    report = json.loads((tmp_path / "challenge.json").read_text(encoding="utf-8"))
    prompt = (tmp_path / "challenge_prompt.md").read_text(encoding="utf-8")
    assert report["idea_id"] == idea.id
    assert len(report["nearest_matches"]) == 5
    assert "prior_art_claim" in prompt
