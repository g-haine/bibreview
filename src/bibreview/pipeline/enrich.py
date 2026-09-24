"""Compose CrossRef, publisher, and optional abstract fallback metadata.

This module owns enrichment policy, not network access. Provider adapters stay
independent and are injected into :class:`EnrichmentService`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ..providers.base import Enrichment, EnrichmentProvider
from ..reporting import Reporter
from ..text import clean_metadata, normalize_provider_abstract


class AbstractFallbackProvider(Protocol):
    """Minimal contract required from the run-scoped abstract fallback."""

    def abstract(self, doi: str) -> str:
        """Return a fallback abstract or an explicit unavailable marker."""

    def abstract_many(self, dois: tuple[str, ...]) -> Mapping[str, str]:
        """Return fallback abstracts for multiple DOI values."""


def _prepared_abstract(
    value: str,
    *,
    reporter: Reporter | None = None,
    context: str = "Provider abstract",
) -> str:
    """Return one safe provider abstract, warning when structured markup is refused."""
    result = normalize_provider_abstract(value)
    if result.deterministic:
        return result.normalized
    if reporter is not None:
        reporter.warning(
            f"{context} contains unsupported structured markup "
            f"({result.reason}); ignoring this abstract candidate."
        )
    return ""


def crossref_enrichment(
    message: Mapping[str, Any],
    *,
    reporter: Reporter | None = None,
    doi: str = "",
) -> Enrichment:
    """Extract safely normalized enrichment fields from a CrossRef work message."""
    raw_abstract = str(message.get("abstract") or "")
    context = f"CrossRef abstract for {doi}" if doi else "CrossRef abstract"
    abstract = _prepared_abstract(
        raw_abstract,
        reporter=reporter,
        context=context,
    )

    raw_subjects = message.get("subject")
    keywords: list[str] = []
    if isinstance(raw_subjects, list):
        for value in raw_subjects:
            cleaned = clean_metadata(str(value or "")).strip()
            if cleaned:
                keywords.append(cleaned)

    return Enrichment(abstract=abstract, keywords=tuple(keywords))


def _clean_enrichment(
    value: Enrichment,
    *,
    reporter: Reporter | None = None,
    context: str = "Provider abstract",
) -> Enrichment:
    abstract = _prepared_abstract(
        value.abstract,
        reporter=reporter,
        context=context,
    )
    keywords = tuple(
        cleaned
        for keyword in value.keywords
        if (cleaned := clean_metadata(keyword).strip())
    )
    event = clean_metadata(value.event).strip()
    return Enrichment(abstract=abstract, keywords=keywords, event=event)


class EnrichmentService:
    """Apply BibReview's project-independent metadata enrichment policy.

    Collection mode uses the configured enrichment precedence: a non-empty
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
        reporter: Reporter | None = None,
    ) -> None:
        self.publisher = publisher
        self.fallback = fallback
        self.reporter = reporter or Reporter(-1)

    def for_collection(self, doi: str, message: Mapping[str, Any]) -> Enrichment:
        """Return enrichment for canonical collection of one publication."""
        return self._compose(doi, message, discovery=False)

    def for_discovery(self, doi: str, message: Mapping[str, Any]) -> Enrichment:
        """Return cumulative enrichment suitable for discovery/relevance checks."""
        return self._compose(doi, message, discovery=True)

    def for_collection_many(
        self,
        messages: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Enrichment]:
        """Return collection enrichment for multiple DOI-backed work records.

        Publisher enrichment remains per DOI because those adapters are routed
        by resolved publisher host. Optional abstract fallback is then batched
        for the subset that still has no abstract.
        """
        prepared: dict[str, tuple[Enrichment, Enrichment]] = {}
        fallback_dois: list[str] = []

        for doi, message in messages.items():
            base = crossref_enrichment(
                message,
                reporter=self.reporter,
                doi=doi,
            )
            extra = (
                self.publisher.enrich(doi)
                if self.publisher is not None
                else Enrichment()
            )
            if not isinstance(extra, Enrichment):
                raise TypeError(
                    "publisher enrichment provider must return Enrichment"
                )
            extra = _clean_enrichment(
                extra,
                reporter=self.reporter,
                context=f"Publisher abstract for {doi}",
            )
            prepared[doi] = (base, extra)
            if not (extra.abstract or base.abstract).strip():
                fallback_dois.append(doi)

        fallback_values: Mapping[str, str] = {}
        if fallback_dois and self.fallback is not None:
            fallback_values = self.fallback.abstract_many(tuple(fallback_dois))

        result: dict[str, Enrichment] = {}
        for doi, (base, extra) in prepared.items():
            abstract = extra.abstract or base.abstract
            if not abstract.strip():
                abstract = fallback_values.get(doi, "")
            result[doi] = _clean_enrichment(
                Enrichment(
                    abstract=abstract,
                    keywords=extra.keywords or base.keywords,
                    event=extra.event,
                ),
                reporter=self.reporter,
                context=f"Fallback abstract for {doi}",
            )
        return result

    def _compose(
        self,
        doi: str,
        message: Mapping[str, Any],
        *,
        discovery: bool,
    ) -> Enrichment:
        base = crossref_enrichment(
            message,
            reporter=self.reporter,
            doi=doi,
        )
        extra = self.publisher.enrich(doi) if self.publisher is not None else Enrichment()
        if not isinstance(extra, Enrichment):
            raise TypeError("publisher enrichment provider must return Enrichment")
        extra = _clean_enrichment(
            extra,
            reporter=self.reporter,
            context=f"Publisher abstract for {doi}",
        )

        if discovery:
            abstract = " ".join(
                part for part in (base.abstract, extra.abstract) if part
            )
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
            ),
            reporter=self.reporter,
            context=f"Fallback abstract for {doi}",
        )
