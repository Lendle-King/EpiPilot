"""Offline contract tests for scholarly search, landscape aggregation and PDF handling."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from epipilot.topic_discovery.cli import main
from epipilot.topic_discovery.fulltext import download_open_access_pdf, extract_pdf_text
from epipilot.topic_discovery.landscape import build_research_landscape
from epipilot.topic_discovery.models import (
    LiteratureProvider,
    LiteratureSearchPage,
    LiteratureSearchQuery,
    LiteratureSearchReport,
    PaperIdentifier,
    PaperReference,
    PaperSourceKind,
    ProviderMetrics,
    TopicSpec,
)
from epipilot.topic_discovery.providers import (
    ArxivProvider,
    HttpResponse,
    LiteratureProviderError,
    OpenAlexProvider,
    OpenReviewProvider,
    SemanticScholarProvider,
)
from epipilot.topic_discovery.service import LiteratureSearchService


class StaticTransport:
    def __init__(self, body: bytes, final_url: str = "https://api.example.test/search") -> None:
        self.body = body
        self.final_url = final_url
        self.calls: list[tuple[str, int]] = []

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        max_bytes: int,
    ) -> HttpResponse:
        self.calls.append((url, max_bytes))
        if len(self.body) > max_bytes:
            raise LiteratureProviderError("test response exceeds size limit")
        return HttpResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=self.body,
            final_url=self.final_url,
        )


class FakeExtractor:
    def extract(
        self,
        pdf_path: Path,
        *,
        max_pages: int,
        max_characters: int,
    ) -> tuple[str, int]:
        assert pdf_path.is_file()
        return "A short extracted paper body.", min(2, max_pages)


class FakeProvider:
    provider = LiteratureProvider.OPENALEX

    def __init__(self, paper: PaperReference) -> None:
        self.paper = paper

    def search(self, query: LiteratureSearchQuery) -> LiteratureSearchPage:
        return LiteratureSearchPage(
            provider=self.provider,
            query=query,
            papers=[self.paper],
            total_count=1,
            source_url="https://api.openalex.org/works?search=test",
        )


class FailingProvider:
    provider = LiteratureProvider.ARXIV

    def search(self, query: LiteratureSearchQuery) -> LiteratureSearchPage:
        raise LiteratureProviderError("upstream unavailable")


def test_openalex_maps_abstract_authors_metrics_and_references() -> None:
    body = {
        "meta": {"count": 1},
        "results": [
            {
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1234/example",
                "title": "Physical Skill Transfer",
                "publication_year": 2025,
                "publication_date": "2025-04-03",
                "authorships": [
                    {"author": {"display_name": "A. Researcher"}},
                    {"author": {"display_name": "B. Scientist"}},
                ],
                "primary_location": {"source": {"display_name": "ICML"}},
                "cited_by_count": 12,
                "referenced_works": [
                    "https://openalex.org/W1",
                    "https://openalex.org/W2",
                ],
                "abstract_inverted_index": {
                    "Physical": [0],
                    "evidence": [1],
                    "improves": [2],
                    "transfer.": [3],
                },
                "keywords": [{"display_name": "robotics"}],
                "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/2501.00001"},
            }
        ],
    }
    transport = StaticTransport(json.dumps(body).encode())
    page = OpenAlexProvider(transport, api_key="test-secret").search(
        LiteratureSearchQuery(query="physical skill transfer", year_from=2024, year_to=2026)
    )
    paper = page.papers[0]
    assert page.total_count == 1
    assert paper.doi == "10.1234/example"
    assert paper.abstract == "Physical evidence improves transfer."
    assert paper.authors == ["A. Researcher", "B. Scientist"]
    assert paper.venue == "ICML"
    assert paper.provider_metrics[0].citation_count == 12
    assert paper.provider_metrics[0].reference_count == 2
    assert len(paper.references) == 2
    assert paper.pdf_url == "https://arxiv.org/pdf/2501.00001"
    assert "filter=" in transport.calls[0][0]
    assert "from_publication_date%3A2024-01-01" in transport.calls[0][0]
    assert "test-secret" not in page.source_url


def test_semantic_scholar_preserves_provider_specific_ids_and_counts() -> None:
    body = {
        "total": 1,
        "offset": 0,
        "next": None,
        "data": [
            {
                "paperId": "s2-paper-1",
                "title": "Physical Skill Transfer",
                "abstract": "A paper abstract.",
                "year": 2025,
                "authors": [{"name": "A. Researcher"}],
                "venue": "ICML",
                "publicationDate": "2025-04-03",
                "citationCount": 9,
                "referenceCount": 4,
                "externalIds": {"DOI": "10.1234/example", "ArXiv": "2501.00001"},
                "url": "https://www.semanticscholar.org/paper/s2-paper-1",
                "openAccessPdf": {"url": "https://arxiv.org/pdf/2501.00001"},
                "references": [{"paperId": "s2-paper-2"}],
            }
        ],
    }
    transport = StaticTransport(json.dumps(body).encode())
    page = SemanticScholarProvider(transport, api_key="test-key").search(
        LiteratureSearchQuery(query="physical skill transfer", limit=10)
    )
    paper = page.papers[0]
    assert paper.doi == "10.1234/example"
    assert paper.arxiv_id == "2501.00001"
    assert paper.provider_metrics[0].citation_count == 9
    assert paper.provider_metrics[0].reference_count == 4
    assert paper.references[0].value == "s2-paper-2"
    assert "x-api-key" not in transport.calls[0][0]


def test_arxiv_parses_atom_metadata_and_year_filter() -> None:
    atom = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>1</opensearch:totalResults>
      <entry>
        <id>https://arxiv.org/abs/2501.00001v2</id>
        <title>  Contact Aware Skills  </title>
        <summary>Evidence improves transfer.</summary>
        <published>2025-01-02T10:00:00Z</published>
        <author><name>A. Researcher</name></author>
        <category term="cs.RO"/>
        <link title="pdf" type="application/pdf" href="https://arxiv.org/pdf/2501.00001"/>
      </entry>
    </feed>"""
    transport = StaticTransport(atom, "https://export.arxiv.org/api/query")
    page = ArxivProvider(transport).search(
        LiteratureSearchQuery(
            query="contact aware skills",
            year_from=2024,
            year_to=2026,
        )
    )
    assert page.total_count == 1
    assert page.papers[0].arxiv_id == "2501.00001"
    assert page.papers[0].source_kind is PaperSourceKind.PREPRINT
    assert page.papers[0].authors == ["A. Researcher"]
    assert page.papers[0].keywords == ["cs.RO"]
    assert "submittedDate" in transport.calls[0][0]


def test_openreview_parses_public_submission_notes() -> None:
    body = {
        "count": 1,
        "notes": [
            {
                "id": "note-1",
                "forum": "forum-1",
                "cdate": int(datetime(2026, 2, 1, tzinfo=UTC).timestamp() * 1000),
                "content": {
                    "title": {"value": "A New Research Method"},
                    "abstract": {"value": "A useful abstract."},
                    "authors": {"value": ["A. Researcher"]},
                    "venue": {"value": "ICLR 2026"},
                    "keywords": {"value": ["agents", "research"]},
                },
            }
        ],
    }
    transport = StaticTransport(json.dumps(body).encode())
    page = OpenReviewProvider(transport).search(
        LiteratureSearchQuery(query="research method", venue="ICLR", year_from=2026)
    )
    paper = page.papers[0]
    assert paper.year == 2026
    assert paper.title == "A New Research Method"
    assert paper.authors == ["A. Researcher"]
    assert paper.url == "https://openreview.net/forum?id=forum-1"
    assert paper.pdf_url == "https://openreview.net/pdf?id=forum-1"


def test_cross_provider_merge_keeps_provider_metrics_separate() -> None:
    query = LiteratureSearchQuery(query="physical skill transfer")
    openalex = OpenAlexProvider(StaticTransport(b'{"meta":{"count":0},"results":[]}'))
    semantic = SemanticScholarProvider(StaticTransport(b'{"total":0,"data":[]}'))
    paper_a = PaperReference(
        id="a",
        title="Physical Skill Transfer",
        year=2025,
        doi="10.1234/example",
        url="https://doi.org/10.1234/example",
        relevance_note="OpenAlex result with a persistent DOI.",
        provider_ids=[PaperIdentifier(provider=LiteratureProvider.OPENALEX, value="W1")],
        provider_metrics=[ProviderMetrics(provider=LiteratureProvider.OPENALEX, citation_count=10)],
    )
    paper_b = paper_a.model_copy(
        update={
            "id": "b",
            "provider_ids": [
                PaperIdentifier(
                    provider=LiteratureProvider.SEMANTIC_SCHOLAR,
                    value="S2-1",
                )
            ],
            "provider_metrics": [
                ProviderMetrics(
                    provider=LiteratureProvider.SEMANTIC_SCHOLAR,
                    citation_count=7,
                )
            ],
        }
    )
    report = LiteratureSearchService([openalex, semantic]).search(query)
    assert report.papers == []
    merged = LiteratureSearchReport(
        query=query,
        pages=[],
        papers=[paper_a, paper_b],
    )
    from epipilot.topic_discovery.identity import merge_papers

    unique = merge_papers(merged.papers)
    assert len(unique) == 1
    assert {item.provider for item in unique[0].provider_metrics} == {
        LiteratureProvider.OPENALEX,
        LiteratureProvider.SEMANTIC_SCHOLAR,
    }
    assert len(unique[0].provider_ids) == 2


def test_landscape_resolves_known_citation_edges() -> None:
    query = LiteratureSearchQuery(query="citation graph")
    source = PaperReference(
        id="source",
        title="Source Paper",
        year=2025,
        url="https://example.org/source",
        relevance_note="A source paper for the graph test.",
        provider_ids=[PaperIdentifier(provider=LiteratureProvider.OPENALEX, value="W1")],
        references=[PaperIdentifier(provider=LiteratureProvider.OPENALEX, value="W2")],
    )
    target = PaperReference(
        id="target",
        title="Target Paper",
        year=2024,
        url="https://example.org/target",
        relevance_note="A cited target paper for the graph test.",
        provider_ids=[PaperIdentifier(provider=LiteratureProvider.OPENALEX, value="W2")],
    )
    page = LiteratureSearchPage(
        provider=LiteratureProvider.OPENALEX,
        query=query,
        papers=[source, target],
        total_count=2,
        source_url="https://api.openalex.org/works?search=citation",
    )
    report = LiteratureSearchReport(query=query, pages=[page], papers=[source, target])
    topic = TopicSpec(
        id="topic-1",
        title="Citation landscape",
        description="Build a compact citation landscape for a test topic.",
        research_area="Computer Science",
    )
    landscape = build_research_landscape(topic, report)
    assert len(landscape.papers) == 2
    assert landscape.citation_edges[0].target_paper_id == landscape.papers[1].id
    assert landscape.unresolved_reference_count == 0


def test_fulltext_download_requires_allowlisted_https_host(tmp_path: Path) -> None:
    paper = PaperReference(
        id="paper-pdf",
        title="Open Access Paper",
        year=2025,
        url="https://example.org/paper",
        pdf_url="https://arxiv.org/pdf/2501.00001",
        relevance_note="A paper with an explicitly allowlisted PDF.",
    )
    pdf_bytes = b"%PDF-1.4\nminimal test bytes"
    transport = StaticTransport(pdf_bytes, "https://arxiv.org/pdf/2501.00001")
    artifact = download_open_access_pdf(
        paper,
        tmp_path,
        allowed_hosts=["arxiv.org"],
        transport=transport,
    )
    assert Path(artifact.pdf_path).read_bytes() == pdf_bytes
    extracted = extract_pdf_text(artifact, extractor=FakeExtractor())
    assert extracted.text_characters == len("A short extracted paper body.")
    assert Path(extracted.text_path or "").read_text(encoding="utf-8") == (
        "A short extracted paper body."
    )


def test_fulltext_rejects_unapproved_host(tmp_path: Path) -> None:
    paper = PaperReference(
        id="paper-untrusted",
        title="Untrusted PDF",
        year=2025,
        url="https://example.org/paper",
        pdf_url="https://attacker.example/paper.pdf",
        relevance_note="A paper with an unapproved PDF host.",
    )
    with pytest.raises(ValueError, match="not in the explicit allowlist"):
        download_open_access_pdf(
            paper,
            tmp_path,
            allowed_hosts=["arxiv.org"],
            transport=StaticTransport(b"%PDF-1.4", "https://attacker.example/paper.pdf"),
        )


def test_service_preserves_partial_provider_failures() -> None:
    query = LiteratureSearchQuery(query="topic discovery")
    paper = PaperReference(
        id="paper-1",
        title="Topic Discovery",
        year=2025,
        url="https://example.org/topic",
        relevance_note="A minimal paper returned by a fake provider.",
    )
    report = LiteratureSearchService([FakeProvider(paper), FailingProvider()]).search(query)
    assert len(report.pages) == 1
    assert len(report.papers) == 1
    assert report.provider_errors[0].provider is LiteratureProvider.ARXIV


def test_cli_writes_research_landscape_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = PaperReference(
        id="paper-cli",
        title="Embodied Skill Discovery",
        year=2025,
        url="https://example.org/embodied",
        relevance_note="A synthetic paper for offline CLI testing.",
    )
    monkeypatch.setattr(
        "epipilot.topic_discovery.cli.build_default_providers",
        lambda: [FakeProvider(paper)],
    )
    result = main(
        [
            "--root",
            str(tmp_path),
            "search",
            "--query",
            "embodied skill discovery",
            "--provider",
            "openalex",
            "--output",
            "landscape.json",
        ]
    )
    assert result == 0
    payload = json.loads((tmp_path / "landscape.json").read_text(encoding="utf-8"))
    assert payload["landscape"]["papers"][0]["title"] == "Embodied Skill Discovery"


def test_doi_arxiv_alias_bridges_cross_provider_records() -> None:
    from epipilot.topic_discovery.identity import merge_papers

    doi_record = PaperReference(
        id="doi-record",
        title="Alias Connected Paper",
        year=2025,
        doi="10.5555/alias",
        url="https://doi.org/10.5555/alias",
        relevance_note="A DOI-indexed version of the paper.",
        provider_ids=[PaperIdentifier(provider=LiteratureProvider.OPENALEX, value="W-DOI")],
    )
    bridge_record = PaperReference(
        id="bridge-record",
        title="Alias Connected Paper",
        year=2025,
        doi="10.5555/alias",
        arxiv_id="2501.12345",
        url="https://doi.org/10.5555/alias",
        relevance_note="A provider record containing both persistent identifiers.",
        provider_ids=[
            PaperIdentifier(
                provider=LiteratureProvider.SEMANTIC_SCHOLAR,
                value="S2-BRIDGE",
            )
        ],
    )
    arxiv_record = PaperReference(
        id="arxiv-record",
        title="Alias Connected Paper",
        year=2025,
        arxiv_id="2501.12345v2",
        url="https://arxiv.org/abs/2501.12345",
        relevance_note="An arXiv-indexed version of the same paper.",
        provider_ids=[PaperIdentifier(provider=LiteratureProvider.ARXIV, value="2501.12345")],
    )
    merged = merge_papers([doi_record, bridge_record, arxiv_record])
    assert len(merged) == 1
    assert merged[0].doi == "10.5555/alias"
    assert merged[0].arxiv_id == "2501.12345"
    assert len(merged[0].provider_ids) == 3
