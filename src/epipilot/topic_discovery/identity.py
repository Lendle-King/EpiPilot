"""Stable paper identity and deterministic cross-provider merging helpers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Hashable, Iterable
from typing import TypeVar

from epipilot.topic_discovery.models import (
    LiteratureProvider,
    PaperIdentifier,
    PaperReference,
    ProviderMetrics,
)


def normalize_doi(value: str | None) -> str | None:
    """Normalize DOI URLs and prefixes to a case-insensitive DOI identifier."""
    if value is None:
        return None
    doi = value.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix) :]
            break
    if doi.lower().startswith("doi:"):
        doi = doi[4:]
    normalized = doi.strip().lower()
    return normalized or None


def normalize_arxiv_id(value: str | None) -> str | None:
    """Normalize arXiv URLs and version suffixes to the base identifier."""
    if value is None:
        return None
    identifier = value.strip()
    for prefix in (
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
    ):
        if identifier.lower().startswith(prefix):
            identifier = identifier[len(prefix) :]
            break
    identifier = identifier.removesuffix(".pdf")
    identifier = re.sub(r"\.v\d+$", "", identifier)
    return identifier or None


def normalize_title(value: str) -> str:
    """Normalize title punctuation and whitespace for conservative exact deduplication."""
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def canonical_paper_id(
    *,
    title: str,
    year: int,
    doi: str | None = None,
    arxiv_id: str | None = None,
) -> str:
    """Create a stable identifier from the strongest available scholarly identifier."""
    normalized_doi = normalize_doi(doi)
    normalized_arxiv = normalize_arxiv_id(arxiv_id)
    if normalized_doi is not None:
        identity = f"doi:{normalized_doi}"
    elif normalized_arxiv is not None:
        identity = f"arxiv:{normalized_arxiv}"
    else:
        identity = f"title:{normalize_title(title)}:{year}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"paper-{digest}"


def merge_papers(papers: Iterable[PaperReference]) -> list[PaperReference]:
    """Merge only records connected by an exact persistent identifier.

    DOI and arXiv aliases can connect records from different providers. Provider IDs
    remain namespaced; title/year fallback is used only when no persistent ID exists.
    """
    records = list(papers)
    parents = list(range(len(records)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    token_owner: dict[str, int] = {}
    for index, paper in enumerate(records):
        for token in _identity_tokens(paper):
            owner = token_owner.get(token)
            if owner is None:
                token_owner[token] = index
            else:
                union(index, owner)

    groups: dict[int, list[PaperReference]] = {}
    for index, paper in enumerate(records):
        groups.setdefault(find(index), []).append(paper)

    merged: list[PaperReference] = []
    for group in groups.values():
        current = group[0]
        for paper in group[1:]:
            current = _merge_pair(current, paper)
        canonical_id = canonical_paper_id(
            title=current.title,
            year=current.year,
            doi=current.doi,
            arxiv_id=current.arxiv_id,
        )
        merged.append(current.model_copy(update={"id": canonical_id}))
    return merged


def _identity_tokens(paper: PaperReference) -> set[str]:
    tokens: set[str] = set()
    doi = normalize_doi(paper.doi)
    arxiv_id = normalize_arxiv_id(paper.arxiv_id)
    if doi is not None:
        tokens.add(f"doi:{doi}")
    if arxiv_id is not None:
        tokens.add(f"arxiv:{arxiv_id}")
    for identifier in paper.provider_ids:
        tokens.add(f"provider:{identifier.provider.value}:{identifier.value}")
    if not tokens:
        tokens.add(f"title:{normalize_title(paper.title)}:{paper.year}")
    return tokens


def _merge_pair(current: PaperReference, paper: PaperReference) -> PaperReference:
    provider_ids = _unique(
        [*current.provider_ids, *paper.provider_ids],
        key=lambda item: (item.provider, item.value),
    )
    references = _unique(
        [*current.references, *paper.references],
        key=lambda item: (item.provider, item.value),
    )
    metrics = _unique(
        [*current.provider_metrics, *paper.provider_metrics],
        key=lambda item: item.provider,
        prefer_new=True,
    )
    authors = current.authors if len(current.authors) >= len(paper.authors) else paper.authors
    keywords = _unique([*current.keywords, *paper.keywords], key=str.casefold)
    source_kind = (
        current.source_kind
        if current.source_kind.value == "paper" or paper.source_kind.value != "paper"
        else paper.source_kind
    )
    return current.model_copy(
        update={
            "doi": normalize_doi(current.doi) or normalize_doi(paper.doi),
            "arxiv_id": normalize_arxiv_id(current.arxiv_id) or normalize_arxiv_id(paper.arxiv_id),
            "url": current.url or paper.url,
            "pdf_url": current.pdf_url or paper.pdf_url,
            "venue": current.venue or paper.venue,
            "publication_date": current.publication_date or paper.publication_date,
            "source_kind": source_kind,
            "abstract": _longer(current.abstract, paper.abstract),
            "authors": authors,
            "keywords": keywords,
            "provider_ids": provider_ids,
            "provider_metrics": metrics,
            "references": references,
        }
    )


def _longer(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if len(left) >= len(right) else right


T = TypeVar("T")
K = TypeVar("K", bound=Hashable)


def _unique(
    values: Iterable[T],
    *,
    key: Callable[[T], K],
    prefer_new: bool = False,
) -> list[T]:
    """Keep first values by a caller-provided key; optionally prefer the latest value."""
    result: dict[K, T] = {}
    order: list[K] = []
    for value in values:
        identity = key(value)
        if identity not in result:
            order.append(identity)
        if prefer_new or identity not in result:
            result[identity] = value
    return [result[identity] for identity in order]


def provider_identifier(
    provider: LiteratureProvider,
    value: str | None,
) -> PaperIdentifier | None:
    """Build a provider identifier only when the upstream ID is nonempty."""
    if value is None or not value.strip():
        return None
    return PaperIdentifier(provider=provider, value=value.strip())


def merge_provider_metrics(
    provider: LiteratureProvider,
    citation_count: int | None,
    reference_count: int | None,
) -> list[ProviderMetrics]:
    return [
        ProviderMetrics(
            provider=provider,
            citation_count=citation_count,
            reference_count=reference_count,
        )
    ]
