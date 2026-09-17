"""Compose CrossRef, publisher, and optional abstract fallback metadata.

This module owns enrichment policy, not network access. Provider adapters stay
independent and are injected into :class:`EnrichmentService`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ..providers.base import Enrichment, EnrichmentProvider
from ..text import clean_metadata


class AbstractFallbackProvider(Protocol):
    """Minimal contract required from the run-scoped abstract fallback."""

    def abstract(self, doi: str) -> str:
        """Return a fallback abstract or an explicit unavailable marker."""


def crossref_enrichment(message: Mapping[str, Any]) -> Enrichment:
    """Extract the enrichment fields already present in a CrossRef work message."""
    raw_abstract = message.get("abstract")
    abstract = clean_metadata(str(raw_abstract or ""), abstract=True).strip()

    raw_subjects = message.get("subject")
    keywords: list[str] = []
    if isinstance(raw_subjects, list):
        for value in raw_subjects:
            cleaned = clean_metadata(str(value or "")).strip()
            if cleaned:
                keywords.append(cleaned)

    return Enrichment(abstract=abstract, keywords=tuple(keywords))


def _clean_enrichment(value: Enrichment) -> Enrichment:
    abstract = clean_metadata(value.abstract, abstract=True).strip()
    keywords = tuple(
        cleaned
        for keyword in value.keywords
        if (cleaned := clean_metadata(keyword).strip())
    )
    event = clean_metadata(value.event).strip()
    return Enrichment(abstract=abstract, keywords=keywords, event=event)


class EnrichmentService:
    """Apply BibReview's project-independent metadata enrichment policy.

    Collection mode preserves PHRAISE's established precedence: a non-empty
    publisher field replaces the corresponding CrossRef field. Discovery mode
    accumulates CrossRef and publisher text so relevance checks see both sources.
    Optional abstract fallback is consulted only when the resulting abstract is
    empty.
    """

    def __init__(
        self,
        *,
        publisher: EnrichmentProvider | None = None,
        fallback: AbstractFallbackProvider | None = None,
    ) -> None:
        self.publisher = publisher
        self.fallback = fallback

    def for_collection(self, doi: str, message: Mapping[str, Any]) -> Enrichment:
        """Return enrichment for canonical collection of one publication."""
        return self._compose(doi, message, discovery=False)

    def for_discovery(self, doi: str, message: Mapping[str, Any]) -> Enrichment:
        """Return cumulative enrichment suitable for discovery/relevance checks."""
        return self._compose(doi, message, discovery=True)

    def _compose(
        self,
        doi: str,
        message: Mapping[str, Any],
        *,
        discovery: bool,
    ) -> Enrichment:
        base = crossref_enrichment(message)
        extra = self.publisher.enrich(doi) if self.publisher is not None else Enrichment()
        if not isinstance(extra, Enrichment):
            raise TypeError("publisher enrichment provider must return Enrichment")

        if discovery:
            abstract = base.abstract + extra.abstract
            keywords = base.keywords + extra.keywords
        else:
            abstract = extra.abstract or base.abstract
            keywords = extra.keywords or base.keywords

        if not abstract.strip() and self.fallback is not None:
            abstract = self.fallback.abstract(doi)

        return _clean_enrichment(
            Enrichment(
                abstract=abstract,
                keywords=keywords,
                event=extra.event,
            )
        )
