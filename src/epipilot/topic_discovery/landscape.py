"""Deterministic aggregation of literature search results into a research landscape."""

from __future__ import annotations

from collections import Counter

from epipilot.topic_discovery.identity import merge_papers
from epipilot.topic_discovery.models import (
    CitationEdge,
    LiteratureSearchReport,
    ResearchLandscape,
    TopicSpec,
)


def build_research_landscape(
    topic: TopicSpec,
    report: LiteratureSearchReport,
) -> ResearchLandscape:
    """Build a compact, source-traceable landscape without inventing paper claims."""
    papers = merge_papers(report.papers)
    provider_counts: Counter[str] = Counter()
    year_counts: Counter[int] = Counter()
    identifier_to_paper: dict[tuple[str, str], str] = {}

    for paper in papers:
        year_counts[paper.year] += 1
        for identifier in paper.provider_ids:
            provider_counts[identifier.provider.value] += 1
            identifier_to_paper[(identifier.provider.value, identifier.value)] = paper.id

    edges: list[CitationEdge] = []
    edge_keys: set[tuple[str, str, str]] = set()
    unresolved = 0
    for paper in papers:
        for reference in paper.references:
            target = identifier_to_paper.get(
                (reference.provider.value, reference.value)
            )
            key = (paper.id, reference.provider.value, reference.value)
            if key in edge_keys:
                continue
            edge_keys.add(key)
            if target is None:
                unresolved += 1
            edges.append(
                CitationEdge(
                    source_paper_id=paper.id,
                    target_identifier=reference,
                    target_paper_id=target,
                )
            )

    warnings = [
        f"{error.provider.value}: {error.message}" for error in report.provider_errors
    ]
    if not papers:
        warnings.append("No papers were returned; inspect query wording and provider coverage.")
    return ResearchLandscape(
        topic_id=topic.id,
        query=report.query,
        papers=papers,
        citation_edges=edges,
        provider_counts=dict(provider_counts),
        year_counts=dict(year_counts),
        unresolved_reference_count=unresolved,
        provider_errors=report.provider_errors,
        warnings=warnings,
    )


def search_report_summary(report: LiteratureSearchReport) -> dict[str, object]:
    """Return a compact operational summary without discarding the full report."""
    return {
        "query": report.query.model_dump(mode="json"),
        "provider_successes": [page.provider.value for page in report.pages],
        "provider_errors": [
            {"provider": error.provider.value, "message": error.message}
            for error in report.provider_errors
        ],
        "papers_before_deduplication": sum(len(page.papers) for page in report.pages),
        "unique_papers": len(report.papers),
        "retrieved_at": report.retrieved_at.isoformat(),
    }

