"""Small, replaceable adapters for public scholarly literature APIs."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from epipilot.topic_discovery.identity import (
    canonical_paper_id,
    merge_provider_metrics,
    normalize_arxiv_id,
    normalize_doi,
    provider_identifier,
)
from epipilot.topic_discovery.models import (
    LiteratureProvider,
    LiteratureSearchPage,
    LiteratureSearchQuery,
    PaperReference,
    PaperSourceKind,
)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str


class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        max_bytes: int,
    ) -> HttpResponse: ...


class LiteratureProviderError(RuntimeError):
    """An upstream API failure with a bounded, non-sensitive diagnostic."""


class UrllibTransport:
    """Standard-library HTTP transport with response-size and timeout limits."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        max_bytes: int,
    ) -> HttpResponse:
        request_headers = {"User-Agent": "EpiPilot-TopicDiscovery/0.1"}
        request_headers.update(headers)
        request = urllib.request.Request(url, headers=request_headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise LiteratureProviderError("upstream response exceeds configured size limit")
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                return HttpResponse(
                    status=response.status,
                    headers=response_headers,
                    body=body,
                    final_url=response.geturl(),
                )
        except urllib.error.HTTPError as exc:
            raise LiteratureProviderError(f"upstream returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LiteratureProviderError(f"upstream request failed: {type(exc).__name__}") from exc


class SearchProvider(Protocol):
    provider: LiteratureProvider

    def search(self, query: LiteratureSearchQuery) -> LiteratureSearchPage: ...


class BaseSearchProvider:
    provider: LiteratureProvider
    base_url: str
    timeout_seconds = 20.0
    max_response_bytes = 8 * 1024 * 1024

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self.transport = transport or UrllibTransport()

    def _get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> HttpResponse:
        return self.transport.get(
            url,
            headers=headers or {},
            timeout=self.timeout_seconds,
            max_bytes=max_bytes or self.max_response_bytes,
        )

    @staticmethod
    def _url(base: str, params: Mapping[str, object]) -> str:
        encoded = urllib.parse.urlencode(
            {key: value for key, value in params.items() if value is not None}
        )
        return f"{base}?{encoded}"


class OpenAlexProvider(BaseSearchProvider):
    provider = LiteratureProvider.OPENALEX
    base_url = "https://api.openalex.org/works"
    fields = ",".join(
        (
            "id",
            "doi",
            "title",
            "publication_year",
            "publication_date",
            "authorships",
            "primary_location",
            "cited_by_count",
            "referenced_works",
            "abstract_inverted_index",
            "keywords",
            "best_oa_location",
            "type",
        )
    )

    def __init__(
        self,
        transport: HttpTransport | None = None,
        *,
        api_key: str | None = None,
    ) -> None:
        super().__init__(transport)
        self.api_key = api_key

    def search(self, query: LiteratureSearchQuery) -> LiteratureSearchPage:
        page_number = query.offset // query.limit + 1
        skip = query.offset % query.limit
        filters: list[str] = []
        if query.year_from is not None:
            filters.append(f"from_publication_date:{query.year_from}-01-01")
        if query.year_to is not None:
            filters.append(f"to_publication_date:{query.year_to}-12-31")
        params: dict[str, object] = {
            "search": query.query,
            "per-page": query.limit,
            "page": page_number,
            "select": self.fields,
        }
        if filters:
            params["filter"] = ",".join(filters)
        if self.api_key:
            params["api_key"] = self.api_key
        request_url = self._url(self.base_url, params)
        public_params = {key: value for key, value in params.items() if key != "api_key"}
        source_url = self._url(self.base_url, public_params)
        response = self._get(request_url)
        payload = _json_object(response.body, self.provider)
        meta = _as_dict(payload.get("meta"))
        raw_results = _as_list(payload.get("results"))
        papers = [
            paper for item in raw_results if (paper := self._paper(_as_dict(item))) is not None
        ][skip:]
        total = _as_int(meta.get("count"))
        next_offset: int | None = query.offset + len(papers)
        if total is None or next_offset >= total:
            next_offset = None
        return LiteratureSearchPage(
            provider=self.provider,
            query=query,
            papers=papers,
            total_count=total,
            next_offset=next_offset,
            source_url=source_url,
        )

    def _paper(self, item: dict[str, object]) -> PaperReference | None:
        title = _as_str(item.get("title"))
        year = _as_int(item.get("publication_year"))
        openalex_id = _as_str(item.get("id"))
        if not title or year is None or not openalex_id:
            return None

        doi = normalize_doi(_as_str(item.get("doi")))
        identifier = provider_identifier(self.provider, openalex_id)
        authorships = _as_list(item.get("authorships"))
        authors = [
            name
            for authorship in authorships
            if (name := _as_str(_as_dict(_as_dict(authorship).get("author")).get("display_name")))
        ]
        location = _as_dict(item.get("primary_location"))
        source = _as_dict(location.get("source"))
        best_location = _as_dict(item.get("best_oa_location"))
        pdf_url = _as_str(best_location.get("pdf_url"))
        raw_references = _as_list(item.get("referenced_works"))
        references = [
            reference
            for raw in raw_references[:500]
            if (reference := provider_identifier(self.provider, _as_str(raw))) is not None
        ]
        keywords = [
            name
            for keyword in _as_list(item.get("keywords"))
            if (name := _as_str(_as_dict(keyword).get("display_name")))
        ]
        publication_date = _as_str(item.get("publication_date"))
        return PaperReference(
            id=canonical_paper_id(
                title=title,
                year=year,
                doi=doi,
                provider_ids=[identifier] if identifier is not None else [],
            ),
            title=title,
            year=year,
            source_kind=PaperSourceKind.PAPER,
            venue=_as_str(source.get("display_name")),
            doi=doi,
            url=doi_url(doi) or openalex_id,
            pdf_url=pdf_url,
            relevance_note="Retrieved from OpenAlex works search.",
            abstract=_reconstruct_abstract(item.get("abstract_inverted_index")),
            authors=authors,
            publication_date=publication_date,
            keywords=keywords,
            provider_ids=[identifier] if identifier is not None else [],
            provider_metrics=merge_provider_metrics(
                self.provider,
                _as_int(item.get("cited_by_count")),
                len(raw_references),
            ),
            references=references,
        )


class SemanticScholarProvider(BaseSearchProvider):
    provider = LiteratureProvider.SEMANTIC_SCHOLAR
    base_url = "https://api.semanticscholar.org/graph/v1/paper/search"
    fields = (
        "title,abstract,year,authors,venue,publicationDate,citationCount,"
        "referenceCount,externalIds,url,openAccessPdf,references"
    )

    def __init__(
        self,
        transport: HttpTransport | None = None,
        *,
        api_key: str | None = None,
    ) -> None:
        super().__init__(transport)
        self.api_key = api_key

    def search(self, query: LiteratureSearchQuery) -> LiteratureSearchPage:
        year_filter = None
        if query.year_from is not None or query.year_to is not None:
            year_filter = f"{query.year_from or 1900}-{query.year_to or datetime.now(UTC).year}"
        params: dict[str, object] = {
            "query": query.query,
            "limit": query.limit,
            "offset": query.offset,
            "fields": self.fields,
            "year": year_filter,
        }
        url = self._url(self.base_url, params)
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        response = self._get(url, headers=headers)
        payload = _json_object(response.body, self.provider)
        raw_results = _as_list(payload.get("data"))
        papers = [
            paper for item in raw_results if (paper := self._paper(_as_dict(item))) is not None
        ]
        total = _as_int(payload.get("total"))
        next_offset = _as_int(payload.get("next"))
        return LiteratureSearchPage(
            provider=self.provider,
            query=query,
            papers=papers,
            total_count=total,
            next_offset=next_offset,
            source_url=url,
        )

    def _paper(self, item: dict[str, object]) -> PaperReference | None:
        title = _as_str(item.get("title"))
        year = _as_int(item.get("year"))
        paper_id = _as_str(item.get("paperId"))
        if not title or year is None or not paper_id:
            return None
        external = _as_dict(item.get("externalIds"))
        doi = normalize_doi(_as_str(external.get("DOI")))
        arxiv_id = normalize_arxiv_id(_as_str(external.get("ArXiv")))
        authors = [
            name
            for author in _as_list(item.get("authors"))
            if (name := _as_str(_as_dict(author).get("name")))
        ]
        references = [
            reference
            for raw in _as_list(item.get("references"))[:500]
            if (
                reference := provider_identifier(
                    self.provider,
                    _as_str(_as_dict(raw).get("paperId")),
                )
            )
            is not None
        ]
        open_access = _as_dict(item.get("openAccessPdf"))
        pdf_url = _as_str(open_access.get("url"))
        identifier = provider_identifier(self.provider, paper_id)
        return PaperReference(
            id=canonical_paper_id(
                title=title,
                year=year,
                doi=doi,
                arxiv_id=arxiv_id,
                provider_ids=[identifier] if identifier is not None else [],
            ),
            title=title,
            year=year,
            source_kind=PaperSourceKind.PREPRINT if arxiv_id else PaperSourceKind.PAPER,
            venue=_as_str(item.get("venue")),
            doi=doi,
            arxiv_id=arxiv_id,
            url=(
                doi_url(doi)
                or arxiv_url(arxiv_id)
                or _as_str(item.get("url"))
                or f"https://www.semanticscholar.org/paper/{paper_id}"
            ),
            pdf_url=pdf_url,
            relevance_note="Retrieved from Semantic Scholar paper search.",
            abstract=_as_str(item.get("abstract")),
            authors=authors,
            publication_date=_as_str(item.get("publicationDate")),
            provider_ids=[identifier] if identifier is not None else [],
            provider_metrics=merge_provider_metrics(
                self.provider,
                _as_int(item.get("citationCount")),
                _as_int(item.get("referenceCount")),
            ),
            references=references,
        )


class ArxivProvider(BaseSearchProvider):
    provider = LiteratureProvider.ARXIV
    base_url = "https://export.arxiv.org/api/query"
    atom_ns = "{http://www.w3.org/2005/Atom}"
    arxiv_ns = "{http://arxiv.org/schemas/atom}"

    def search(self, query: LiteratureSearchQuery) -> LiteratureSearchPage:
        search_expression = f'all:"{query.query.replace(chr(34), " ")}"'
        if query.year_from is not None or query.year_to is not None:
            start_year = query.year_from or 1900
            end_year = query.year_to or datetime.now(UTC).year
            date_filter = f"submittedDate:[{start_year}01010000 TO {end_year}12312359]"
            search_expression += f" AND {date_filter}"
        params = {
            "search_query": search_expression,
            "start": query.offset,
            "max_results": query.limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        url = self._url(self.base_url, params)
        response = self._get(url, max_bytes=self.max_response_bytes)
        try:
            root = ET.fromstring(response.body)
        except ET.ParseError as exc:
            raise LiteratureProviderError("arXiv returned invalid Atom XML") from exc

        papers: list[PaperReference] = []
        for entry in root.findall(f"{self.atom_ns}entry"):
            paper = self._paper(entry)
            if paper is not None and _within_years(paper.year, query):
                papers.append(paper)
        total = _as_int(
            root.findtext(
                "opensearch:totalResults",
                namespaces={"opensearch": "http://a9.com/-/spec/opensearch/1.1/"},
            )
        )
        next_offset = query.offset + len(papers)
        if len(papers) < query.limit or (total is not None and next_offset >= total):
            next_offset = None
        return LiteratureSearchPage(
            provider=self.provider,
            query=query,
            papers=papers,
            total_count=total,
            next_offset=next_offset,
            source_url=url,
        )

    def _paper(self, entry: ET.Element) -> PaperReference | None:
        title = _clean_text(entry.findtext(f"{self.atom_ns}title"))
        summary = _clean_text(entry.findtext(f"{self.atom_ns}summary"))
        identifier_url = entry.findtext(f"{self.atom_ns}id")
        arxiv_id = normalize_arxiv_id(identifier_url)
        published = entry.findtext(f"{self.atom_ns}published")
        year = _year_from_date(published)
        if not title or not arxiv_id or year is None:
            return None
        authors = [
            _clean_text(author.findtext(f"{self.atom_ns}name"))
            for author in entry.findall(f"{self.atom_ns}author")
        ]
        categories = [
            category.attrib["term"]
            for category in entry.findall(f"{self.atom_ns}category")
            if category.attrib.get("term")
        ]
        pdf_url = None
        for link in entry.findall(f"{self.atom_ns}link"):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href")
                break
        identifier = provider_identifier(self.provider, arxiv_id)
        return PaperReference(
            id=canonical_paper_id(
                title=title,
                year=year,
                arxiv_id=arxiv_id,
                provider_ids=[identifier] if identifier is not None else [],
            ),
            title=title,
            year=year,
            source_kind=PaperSourceKind.PREPRINT,
            arxiv_id=arxiv_id,
            url=arxiv_url(arxiv_id),
            pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
            relevance_note="Retrieved from the arXiv Atom API.",
            abstract=summary or None,
            authors=[author for author in authors if author],
            publication_date=published,
            keywords=categories,
            provider_ids=[identifier] if identifier is not None else [],
        )


class OpenReviewProvider(BaseSearchProvider):
    provider = LiteratureProvider.OPENREVIEW
    base_url = "https://api2.openreview.net/notes/search"

    def __init__(
        self,
        transport: HttpTransport | None = None,
        *,
        access_token: str | None = None,
    ) -> None:
        super().__init__(transport)
        self.access_token = access_token

    def search(self, query: LiteratureSearchQuery) -> LiteratureSearchPage:
        params: dict[str, object] = {
            "query": query.query,
            "content": "all",
            "source": "forum",
            "limit": query.limit,
            "offset": query.offset,
            "count": "true",
        }
        if query.venue:
            params["venue"] = query.venue
        url = self._url(self.base_url, params)
        headers = (
            {"Cookie": f"openreview.accessToken={self.access_token}"} if self.access_token else {}
        )
        response = self._get(url, headers=headers)
        payload = _json_object(response.body, self.provider)
        raw_notes = _as_list(payload.get("notes"))
        papers = [
            paper
            for item in raw_notes
            if (paper := self._paper(_as_dict(item))) is not None
            and _within_years(paper.year, query)
        ]
        total = _as_int(payload.get("count"))
        next_offset: int | None = query.offset + len(raw_notes)
        if total is None or next_offset >= total or len(raw_notes) < query.limit:
            next_offset = None
        return LiteratureSearchPage(
            provider=self.provider,
            query=query,
            papers=papers,
            total_count=total,
            next_offset=next_offset,
            source_url=url,
        )

    def _paper(self, note: dict[str, object]) -> PaperReference | None:
        content = _as_dict(note.get("content"))
        title = _content_string(content, "title")
        abstract = _content_string(content, "abstract")
        forum_id = _as_str(note.get("forum")) or _as_str(note.get("id"))
        if not title or not forum_id:
            return None
        venue = _content_string(content, "venue")
        year = _year_from_timestamp(note.get("cdate")) or _year_from_text(venue)
        if year is None:
            return None
        authors_value = _content_value(content, "authors")
        authors = [value for item in _as_list(authors_value) if (value := _as_str(item))]
        keywords_value = _content_value(content, "keywords")
        keywords = [value for item in _as_list(keywords_value) if (value := _as_str(item))]
        identifier = provider_identifier(self.provider, forum_id)
        return PaperReference(
            id=canonical_paper_id(
                title=title,
                year=year,
                provider_ids=[identifier] if identifier is not None else [],
            ),
            title=title,
            year=year,
            source_kind=PaperSourceKind.PAPER,
            venue=venue,
            url=f"https://openreview.net/forum?id={urllib.parse.quote(forum_id)}",
            pdf_url=f"https://openreview.net/pdf?id={urllib.parse.quote(forum_id)}",
            relevance_note="Retrieved from the public OpenReview notes search endpoint.",
            abstract=abstract,
            authors=authors,
            keywords=keywords,
            provider_ids=[identifier] if identifier is not None else [],
        )


def build_default_providers(
    transport: HttpTransport | None = None,
) -> list[SearchProvider]:
    """Create keyless public providers, using optional API keys from the environment."""
    return [
        OpenAlexProvider(transport, api_key=os.environ.get("OPENALEX_API_KEY")),
        SemanticScholarProvider(
            transport,
            api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
        ),
        ArxivProvider(transport),
        OpenReviewProvider(transport, access_token=os.environ.get("OPENREVIEW_ACCESS_TOKEN")),
    ]


def _json_object(body: bytes, provider: LiteratureProvider) -> dict[str, object]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiteratureProviderError(f"{provider.value} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise LiteratureProviderError(f"{provider.value} returned a non-object JSON response")
    return cast(dict[str, object], value)


def _as_dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _as_str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _content_value(content: dict[str, object], key: str) -> object:
    value = content.get(key)
    if isinstance(value, dict):
        return value.get("value")
    return value


def _content_string(content: dict[str, object], key: str) -> str | None:
    return _as_str(_content_value(content, key))


def _reconstruct_abstract(value: object) -> str | None:
    inverted = _as_dict(value)
    positions: list[tuple[int, str]] = []
    for token, raw_positions in inverted.items():
        for position in _as_list(raw_positions):
            if isinstance(position, int) and not isinstance(position, bool):
                positions.append((position, token))
    if not positions:
        return None
    positions.sort()
    return " ".join(token for _, token in positions)


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _year_from_date(value: str | None) -> int | None:
    if value is None or len(value) < 4:
        return None
    return _as_int(value[:4])


def _year_from_timestamp(value: object) -> int | None:
    timestamp = _as_int(value)
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp / 1000, tz=UTC).year
    except (OverflowError, OSError, ValueError):
        return None


def _year_from_text(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"\b(19|20|21)\d{2}\b", value)
    return int(match.group(0)) if match else None


def _within_years(year: int, query: LiteratureSearchQuery) -> bool:
    return not (
        (query.year_from is not None and year < query.year_from)
        or (query.year_to is not None and year > query.year_to)
    )


def doi_url(doi: str | None) -> str | None:
    return f"https://doi.org/{doi}" if doi else None


def arxiv_url(arxiv_id: str | None) -> str | None:
    return f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None
