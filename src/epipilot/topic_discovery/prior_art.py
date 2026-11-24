"""Adversarial prior-art retrieval, ranking, prompt construction and review validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from epipilot.core.models import utc_now
from epipilot.topic_discovery.identity import merge_papers
from epipilot.topic_discovery.models import (
    AuditVerdict,
    DiscoveryWorkspace,
    IdeaCandidate,
    LiteratureProvider,
    LiteratureSearchQuery,
    NoveltyAudit,
    NoveltyDifference,
    PaperReference,
)
from epipilot.topic_discovery.providers import SearchProvider
from epipilot.topic_discovery.v02_models import (
    PriorArtChallengeReport,
    PriorArtComparison,
    PriorArtMatch,
    PriorArtReview,
    PriorArtSearchFailure,
)


_RANKING_METHOD = "weighted_field_coverage_v1"
_TOKEN_PATTERN = re.compile(r"[^\W_]{2,}", flags=re.UNICODE)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "based",
    "be",
    "by",
    "can",
    "for",
    "from",
    "how",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "using",
    "what",
    "when",
    "which",
    "with",
}


def build_prior_art_queries(idea: IdeaCandidate, *, max_queries: int = 4) -> list[str]:
    """Derive bounded, transparent queries from distinct parts of the candidate."""
    if max_queries < 1:
        raise ValueError("max_queries must be positive")
    fields = (
        idea.title,
        idea.research_question,
        idea.proposed_mechanism,
        idea.expected_contribution,
    )
    queries: list[str] = []
    seen: set[str] = set()
    for value in fields:
        query = re.sub(r"\s+", " ", value).strip()
        query = query[:240].rsplit(" ", 1)[0] if len(query) > 240 else query
        key = query.casefold()
        if len(query) >= 8 and key not in seen:
            queries.append(query)
            seen.add(key)
        if len(queries) >= max_queries:
            break
    if not queries:
        raise ValueError("candidate does not contain enough text to construct prior-art queries")
    return queries


class PriorArtChallengeService:
    """Search multiple scholarly providers and rank likely prior art lexically."""

    def __init__(
        self,
        providers: Sequence[SearchProvider],
        *,
        max_queries: int = 4,
        limit_per_query: int = 20,
        top_k: int = 12,
        max_unique_papers: int = 500,
        max_pages_per_provider_query: int = 3,
    ) -> None:
        if not providers:
            raise ValueError("at least one literature provider is required")
        if not 1 <= max_queries <= 8:
            raise ValueError("max_queries must be between 1 and 8")
        if not 1 <= limit_per_query <= 100:
            raise ValueError("limit_per_query must be between 1 and 100")
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        if max_unique_papers < top_k:
            raise ValueError("max_unique_papers must be at least top_k")
        if not 1 <= max_pages_per_provider_query <= 20:
            raise ValueError("max_pages_per_provider_query must be between 1 and 20")
        provider_ids = [provider.provider for provider in providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("prior-art providers must be unique")
        self.providers = tuple(providers)
        self.max_queries = max_queries
        self.limit_per_query = limit_per_query
        self.top_k = top_k
        self.max_unique_papers = max_unique_papers
        self.max_pages_per_provider_query = max_pages_per_provider_query

    def challenge(
        self,
        idea: IdeaCandidate,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
        selected_providers: set[LiteratureProvider] | None = None,
    ) -> PriorArtChallengeReport:
        if selected_providers is not None and not selected_providers:
            raise ValueError("select at least one literature provider")
        queries = build_prior_art_queries(idea, max_queries=self.max_queries)
        configured = {provider.provider for provider in self.providers}
        if selected_providers is not None:
            unknown = selected_providers - configured
            if unknown:
                names = sorted(provider.value for provider in unknown)
                raise ValueError(f"selected providers are not configured: {names}")
        selected = [
            provider
            for provider in self.providers
            if selected_providers is None or provider.provider in selected_providers
        ]
        if not selected:
            raise ValueError("none of the selected providers are configured")
        all_papers: list[PaperReference] = []
        successful: set[str] = set()
        failures: list[PriorArtSearchFailure] = []
        pagination_warnings: list[str] = []
        for query_text in queries:
            base_query = LiteratureSearchQuery(
                query=query_text,
                year_from=year_from,
                year_to=year_to,
                limit=self.limit_per_query,
            )
            for provider in selected:
                offset = base_query.offset
                for page_index in range(self.max_pages_per_provider_query):
                    query = base_query.model_copy(update={"offset": offset})
                    try:
                        page = provider.search(query)
                    except (ValueError, OSError, RuntimeError) as exc:
                        failures.append(
                            PriorArtSearchFailure(
                                provider=provider.provider.value,
                                query=query_text,
                                message=f"{type(exc).__name__}: {exc}",
                            )
                        )
                        break
                    successful.add(page.provider.value)
                    all_papers.extend(page.papers)
                    if page.next_offset is None:
                        break
                    if page.next_offset <= offset:
                        failures.append(
                            PriorArtSearchFailure(
                                provider=provider.provider.value,
                                query=query_text,
                                message="provider pagination did not advance the offset",
                            )
                        )
                        break
                    if page_index == self.max_pages_per_provider_query - 1:
                        pagination_warnings.append(
                            f"{provider.provider.value} reached the page cap "
                            f"for query: {query_text}"
                        )
                        break
                    offset = page.next_offset

        merged_papers = merge_papers(all_papers)
        unique_papers = merged_papers[: self.max_unique_papers]
        matches = rank_prior_art(idea, unique_papers, top_k=self.top_k)
        attempted = [
            provider.provider.value
            for provider in self.providers
            if selected_providers is None or provider.provider in selected_providers
        ]
        if not attempted:
            raise ValueError("none of the selected providers are configured")
        warnings: list[str] = list(pagination_warnings)
        if len(merged_papers) > self.max_unique_papers:
            warnings.append(
                "The unique-paper cap was reached; some retrieved papers "
                "were excluded from ranking."
            )
        if len(successful) < 2:
            warnings.append(
                "Fewer than two providers returned results; prior-art coverage is weak."
            )
        if len(unique_papers) < 5:
            warnings.append(
                "Fewer than five unique papers were retrieved; novelty cannot be assessed."
            )
        if not any(paper.abstract for paper in unique_papers):
            warnings.append(
                "No abstracts were retrieved; nearest-neighbor ranking has limited evidence."
            )
        if not matches:
            warnings.append(
                "No lexical matches were found; revise the search terms or inspect coverage."
            )

        retrieved_at = utc_now()
        digest = hashlib.sha256(
            f"{idea.id}:{'|'.join(queries)}:{retrieved_at.isoformat()}".encode("utf-8")
        ).hexdigest()[:16]
        return PriorArtChallengeReport(
            id=f"challenge-{digest}",
            idea_id=idea.id,
            search_queries=queries,
            providers_attempted=attempted,
            providers_succeeded=sorted(successful),
            failures=failures,
            papers=unique_papers,
            nearest_matches=matches,
            ranking_method=_RANKING_METHOD,
            warnings=warnings,
            retrieved_at=retrieved_at,
        )


def rank_prior_art(
    idea: IdeaCandidate,
    papers: Sequence[PaperReference],
    *,
    top_k: int = 12,
) -> list[PriorArtMatch]:
    """Rank candidates by transparent weighted lexical coverage, not semantic similarity."""
    if top_k < 1:
        raise ValueError("top_k must be positive")
    query_terms = _tokens(
        " ".join(
            (
                idea.title,
                idea.research_question,
                idea.proposed_mechanism,
                idea.expected_contribution,
            )
        )
    )
    if not query_terms or not papers:
        return []

    document_fields = [
        {
            "title": set(_tokens(paper.title)),
            "abstract": set(_tokens(paper.abstract or "")),
            "keywords": set(_tokens(" ".join(paper.keywords))),
        }
        for paper in papers
    ]
    document_frequency: Counter[str] = Counter()
    for fields in document_fields:
        document_frequency.update(set().union(*fields.values()))
    count = len(papers)
    term_weights = {
        term: math.log(1 + (count + 0.5) / (document_frequency[term] + 0.5)) for term in query_terms
    }
    total_weight = sum(term_weights.values())
    weighted_fields = (("title", 0.60), ("abstract", 0.30), ("keywords", 0.10))

    scored: list[tuple[float, PaperReference, float, float, float, list[str], list[str]]] = []
    for paper, fields in zip(papers, document_fields, strict=True):
        field_scores: dict[str, float] = {}
        available_weights = 0.0
        weighted_score = 0.0
        matched_terms: set[str] = set()
        matched_fields: list[str] = []
        for field_name, field_weight in weighted_fields:
            if field_name == "abstract" and not fields[field_name]:
                continue
            if field_name == "keywords" and not fields[field_name]:
                continue
            available_weights += field_weight
            matches = query_terms & fields[field_name]
            score = (
                sum(term_weights[term] for term in matches) / total_weight if total_weight else 0.0
            )
            field_scores[field_name] = score
            weighted_score += field_weight * score
            if matches:
                matched_terms.update(matches)
                matched_fields.append(field_name)
        score = weighted_score / available_weights if available_weights else 0.0
        if score <= 0:
            continue
        scored.append(
            (
                score,
                paper,
                field_scores.get("title", 0.0),
                field_scores.get("abstract", 0.0),
                field_scores.get("keywords", 0.0),
                sorted(matched_terms),
                matched_fields,
            )
        )

    scored.sort(key=lambda item: (-item[0], item[1].title.casefold(), item[1].id))
    return [
        PriorArtMatch(
            rank=index,
            paper_id=paper.id,
            score=round(score, 6),
            title_score=round(title_score, 6),
            abstract_score=round(abstract_score, 6),
            keyword_score=round(keyword_score, 6),
            matched_terms=matched_terms,
            matched_fields=matched_fields,
            abstract_available=bool(paper.abstract),
        )
        for index, (
            score,
            paper,
            title_score,
            abstract_score,
            keyword_score,
            matched_terms,
            matched_fields,
        ) in enumerate(scored[:top_k], start=1)
    ]


def build_prior_art_challenge_prompt(
    idea: IdeaCandidate,
    report: PriorArtChallengeReport,
    *,
    max_abstract_characters: int = 4000,
) -> str:
    """Build an adversarial review prompt from retrieved, source-linked nearest works."""
    if report.idea_id != idea.id:
        raise ValueError("prior-art report references a different idea")
    if max_abstract_characters < 100:
        raise ValueError("max_abstract_characters must be at least 100")
    papers = {paper.id: paper for paper in report.papers}
    match_context = []
    for match in report.nearest_matches:
        paper = papers[match.paper_id]
        match_context.append(
            {
                "paper_id": paper.id,
                "title": paper.title,
                "year": paper.year,
                "venue": paper.venue,
                "doi": paper.doi,
                "arxiv_id": paper.arxiv_id,
                "url": paper.url,
                "abstract": (paper.abstract or "")[:max_abstract_characters],
                "retrieval_score": match.score,
                "matched_terms": match.matched_terms,
                "matched_fields": match.matched_fields,
            }
        )
    context = {
        "idea": idea.model_dump(mode="json"),
        "challenge_id": report.id,
        "search_queries": report.search_queries,
        "providers_succeeded": report.providers_succeeded,
        "searched_paper_ids": [paper.id for paper in report.papers],
        "retrieval_warnings": report.warnings,
        "nearest_works": match_context,
    }
    return f"""你是一个严格、怀疑主义的顶会审稿人。你的任务是主动推翻候选课题的原创性主张,而不是帮助作者包装创新。

审查问题:
1. 最近邻工作是否已经解决了同一个研究问题？
2. 候选方法是否只是已有机制的重新命名、组合或工程化？
3. 候选声称的机制差异是否会带来可观测、可证伪的预测差异？
4. 最近邻工作是否已有相同的评价协议、关键变量或理论保证？
5. 还有哪些关键词、相邻领域或替代术语未被检索？
6. 如果摘要证据不足,必须要求全文或补充检索,不能凭标题判断没有重叠。

证据纪律:
- retrieval_score 只是词项覆盖度,不是语义相似度或原创性分数。
- 只能将 supplied nearest_works 中的 paper_id 用作已检索证据。
- supporting_quote 必须逐字复制自对应论文 abstract;没有摘要证据时不要编造 quote。
- 不得声称已证明原创。PROVISIONALLY_DISTINCT 仅表示当前检索范围内暂未发现实质重叠。
- 若成功来源少于两个、检索论文少于五篇,或关键最近邻没有可核验摘要证据,应使用 insufficient_search。
- 每个比较都要说明已有工作主张、候选差异以及差异为何具有实质性。

返回纯 JSON:
{{
  "challenge_id": "{report.id}",
  "idea_id": "{idea.id}",
  "verdict": "insufficient_search | overlap_found | provisionally_distinct",
  "providers_succeeded": {json.dumps(report.providers_succeeded, ensure_ascii=False)},
  "search_queries": {json.dumps(report.search_queries, ensure_ascii=False)},
  "searched_paper_ids": {json.dumps([paper.id for paper in report.papers], ensure_ascii=False)},
  "nearest_work_ids": ["IDs selected from nearest_works"],
  "comparisons": [
    {{
      "nearest_work_id": "paper-id",
      "dimension": "problem | method | mechanism | scenario | insight",
      "prior_art_claim": "what the prior paper actually claims",
      "proposed_difference": "the candidate's precise difference",
      "materiality_rationale": "why this changes the scientific contribution",
      "evidence_locator": "Abstract",
      "supporting_quote": "verbatim sentence from the supplied abstract"
    }}
  ],
  "overlap_risks": ["remaining overlap risks"],
  "blocking_overlap_questions": ["questions that must be resolved before a novelty claim"],
  "reviewer_notes": ["adversarial review notes"],
  "reviewed_by_human": false
}}

待审候选与检索结果:
{json.dumps(context, ensure_ascii=False, indent=2)}
"""


def validate_prior_art_review(
    idea: IdeaCandidate,
    report: PriorArtChallengeReport,
    raw_json: str,
    *,
    source_texts: Mapping[str, str] | None = None,
) -> PriorArtReview:
    """Validate report lineage and verbatim prior-art evidence in a model review."""
    review = PriorArtReview.model_validate_json(raw_json)
    if review.challenge_id != report.id or review.idea_id != idea.id:
        raise ValueError("prior-art review references a different challenge or idea")
    if review.reviewed_by_human:
        raise ValueError("a model-generated review cannot mark itself as human-reviewed")
    if review.providers_succeeded != report.providers_succeeded:
        raise ValueError("prior-art review provider coverage differs from the challenge report")
    if review.search_queries != report.search_queries:
        raise ValueError("prior-art review search queries differ from the challenge report")

    papers = {paper.id: paper for paper in report.papers}
    if set(review.searched_paper_ids) != set(papers):
        raise ValueError("prior-art review must preserve the complete challenge paper set")
    match_ids = {match.paper_id for match in report.nearest_matches}
    if not set(review.nearest_work_ids).issubset(match_ids):
        raise ValueError("prior-art review nearest works must come from ranked search results")

    supplied_texts = source_texts or {}
    for comparison in review.comparisons:
        paper = papers[comparison.nearest_work_id]
        source = supplied_texts.get(paper.id) or paper.abstract or ""
        if _normalize_quote(comparison.supporting_quote) not in _normalize_quote(source):
            raise ValueError(
                f"comparison quote for {paper.id} is not present in supplied source text"
            )
    if review.verdict is AuditVerdict.PROVISIONALLY_DISTINCT:
        if len(report.providers_succeeded) < 2 or len(report.papers) < 5:
            raise ValueError("search coverage is insufficient for a provisionally distinct review")
    return review


def confirm_prior_art_review(
    review: PriorArtReview,
    *,
    human_approved: bool,
) -> NoveltyAudit:
    """Convert a reviewed comparison into a readiness-gate audit after explicit approval."""
    if not human_approved:
        raise ValueError("human approval is required before creating a NoveltyAudit")
    differences = [
        NoveltyDifference(
            dimension=comparison.dimension,
            nearest_work_id=comparison.nearest_work_id,
            prior_art_claim=comparison.prior_art_claim,
            proposed_difference=comparison.proposed_difference,
            materiality_rationale=comparison.materiality_rationale,
            evidence_locator=comparison.evidence_locator,
            supporting_quote=comparison.supporting_quote,
        )
        for comparison in review.comparisons
    ]
    return NoveltyAudit(
        id=f"audit-{review.challenge_id}",
        idea_id=review.idea_id,
        verdict=review.verdict,
        search_platforms=review.providers_succeeded,
        search_queries=review.search_queries,
        searched_paper_ids=review.searched_paper_ids,
        nearest_work_ids=review.nearest_work_ids,
        differences=differences,
        overlap_risks=review.overlap_risks,
        blocking_overlap_questions=review.blocking_overlap_questions,
    )


def attach_prior_art_review(
    workspace: DiscoveryWorkspace,
    report: PriorArtChallengeReport,
    review: PriorArtReview,
    *,
    human_approved: bool,
) -> DiscoveryWorkspace:
    """Attach retrieved papers and a human-approved audit to the existing workspace.

    Call validate_prior_art_review() before this function. This helper preserves the
    workspace's selected-idea gate and rejects paper-ID collisions rather than silently
    rewriting references used by other artifacts.
    """
    if workspace.selected_idea_id != review.idea_id:
        raise ValueError("the reviewed idea must be the workspace's selected idea")
    if report.idea_id != review.idea_id or review.challenge_id != report.id:
        raise ValueError("prior-art report, review and selected idea do not match")
    audit = confirm_prior_art_review(review, human_approved=human_approved)
    existing_audits = {item.id for item in workspace.novelty_audits}
    if audit.id in existing_audits:
        raise ValueError(f"novelty audit already exists: {audit.id}")

    papers_by_id = {paper.id: paper for paper in workspace.papers}
    for paper in report.papers:
        existing = papers_by_id.get(paper.id)
        if existing is not None:
            if existing.title.casefold() != paper.title.casefold() or (
                existing.doi and paper.doi and existing.doi.casefold() != paper.doi.casefold()
            ):
                raise ValueError(f"paper ID collision while attaching prior art: {paper.id}")
            continue
        papers_by_id[paper.id] = paper

    payload = workspace.model_dump(mode="python")
    payload["papers"] = [paper.model_dump(mode="python") for paper in papers_by_id.values()]
    payload["novelty_audits"] = [
        *[item.model_dump(mode="python") for item in workspace.novelty_audits],
        audit.model_dump(mode="python"),
    ]
    return DiscoveryWorkspace.model_validate(payload)


def _tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_PATTERN.findall(value.casefold()) if token not in _STOPWORDS}


def _normalize_quote(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
