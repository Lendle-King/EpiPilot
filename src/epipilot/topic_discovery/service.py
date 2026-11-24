"""Orchestration for multi-provider literature search and conservative deduplication."""

from __future__ import annotations

from collections.abc import Sequence

from epipilot.topic_discovery.identity import merge_papers
from epipilot.topic_discovery.models import (
    LiteratureProvider,
    LiteratureSearchPage,
    LiteratureSearchQuery,
    LiteratureSearchReport,
    ProviderSearchError,
)
from epipilot.topic_discovery.providers import SearchProvider


class LiteratureSearchService:
    """Run selected providers sequentially and preserve partial failures."""

    def __init__(self, providers: Sequence[SearchProvider]) -> None:
        provider_names = [provider.provider for provider in providers]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("literature providers must be unique")
        self.providers = tuple(providers)

    def search(
        self,
        query: LiteratureSearchQuery,
        *,
        selected_providers: set[LiteratureProvider] | None = None,
    ) -> LiteratureSearchReport:
        pages: list[LiteratureSearchPage] = []
        errors: list[ProviderSearchError] = []
        for provider in self.providers:
            if selected_providers is not None and provider.provider not in selected_providers:
                continue
            try:
                pages.append(provider.search(query))
            except (ValueError, OSError, RuntimeError) as exc:
                errors.append(
                    ProviderSearchError(
                        provider=provider.provider,
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )

        papers = merge_papers(paper for page in pages for paper in page.papers)
        return LiteratureSearchReport(
            query=query,
            pages=pages,
            papers=papers,
            provider_errors=errors,
        )
