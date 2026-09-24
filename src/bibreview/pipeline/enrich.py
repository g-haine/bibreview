"""Compose CrossRef, publisher, and optional abstract fallback metadata.

This module owns enrichment policy, not network access. Provider adapters stay
independent and are injected into :class:`EnrichmentService`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ..providers.base import AbstractEvidence, Enrichment, EnrichmentProvider
from ..providers.fallback import AbstractFallbackSelection
from ..reporting import Reporter
from ..text import clean_metadata, normalize_provider_abstract


class AbstractFallbackProvider(Protocol):
    """Minimal contract required from the run-scoped abstract fallback."""

    def abstract(self, doi: str) -> str:
        """Return a fallback abstract or an explicit unavailable marker."""

    def abstract_many(self, dois: tuple[str, ...]) -> Mapping[str, str]:
        """Return fallback abstracts for multiple DOI values."""

    def select(self, doi: str) -> AbstractFallbackSelection:
        """Return one fallback selection with retained refusal evidence."""

    def select_many(
        self,
        dois: tuple[str, ...],
    ) -> Mapping[str, AbstractFallbackSelection]:
        """Return fallback selections with retained refusal evidence."""


def _prepared_abstract(
    value: str,
    *,
    source: str,
    reporter: Reporter | None = None,
    context: str = "Provider abstract",
    preserve_refused: bool = False,
) -> tuple[str, tuple[AbstractEvidence, ...]]:
    """Return safe provider text and retain refused structured markup as evidence."""
    result = normalize_provider_abstract(value)
    if result.deterministic:
        return result.normalized, ()
    evidence = (
        AbstractEvidence(
            source=source,
            value=result.normalized,
            reason=result.reason,
        ),
    )
    if preserve_refused:
        return result.normalized, evidence
    if reporter is not None:
        reporter.warning(
            f"{context} contains unsupported structured markup "
            f"({result.reason}); ignoring this abstract candidate."
        )
    return "", evidence


def crossref_enrichment(
    message: Mapping[str, Any],
    *,
    reporter: Reporter | None = None,
    doi: str = "",
    preserve_refused: bool = False,
) -> Enrichment:
    """Extract safely normalized enrichment fields from a CrossRef work message."""
    raw_abstract = str(message.get("abstract") or "")
    context = f"CrossRef abstract for {doi}" if doi else "CrossRef abstract"
    abstract, evidence = _prepared_abstract(
        raw_abstract,
        source="crossref",
        reporter=reporter,
        context=context,
        preserve_refused=preserve_refused,
    )

    raw_subjects = message.get("subject")
    keywords: list[str] = []
    if isinstance(raw_subjects, list):
        for value in raw_subjects:
            cleaned = clean_metadata(str(value or "")).strip()
            if cleaned:
                keywords.append(cleaned)

    return Enrichment(
        abstract=abstract,
        keywords=tuple(keywords),
        abstract_source="crossref" if abstract else "",
        abstract_evidence=evidence,
    )


def _clean_enrichment(
    value: Enrichment,
    *,
    reporter: Reporter | None = None,
    context: str = "Provider abstract",
    default_source: str = "provider",
    preserve_refused: bool = False,
) -> Enrichment:
    source = value.abstract_source or default_source
    abstract, refused = _prepared_abstract(
        value.abstract,
        source=source,
        reporter=reporter,
        context=context,
        preserve_refused=preserve_refused,
    )
    keywords = tuple(
        cleaned
        for keyword in value.keywords
        if (cleaned := clean_metadata(keyword).strip())
    )
    event = clean_metadata(value.event).strip()
    return Enrichment(
        abstract=abstract,
        keywords=keywords,
        event=event,
        abstract_source=source if abstract else "",
        abstract_evidence=tuple(value.abstract_evidence) + refused,
    )


def _legacy_fallback_selection(value: str) -> AbstractFallbackSelection:
    """Wrap a legacy string-only fallback result."""
    return AbstractFallbackSelection(abstract=value)


class EnrichmentService:
    """Apply BibReview's project-independent metadata enrichment policy.

    Collection mode uses the configured enrichment precedence: a non-empty
    publisher field replaces the corresponding CrossRef field. Discovery mode
    accumulates CrossRef and publisher text so relevance checks see both sources.
    Optional abstract fallback is consulted only when the resulting abstract is
    empty. Refused structured abstracts remain attached as provider evidence.
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

    def _fallback_selection(self, doi: str) -> AbstractFallbackSelection:
        if self.fallback is None:
            return AbstractFallbackSelection()
        select = getattr(self.fallback, "select", None)
        if callable(select):
            result = select(doi)
            if not isinstance(result, AbstractFallbackSelection):
                raise TypeError(
                    "fallback select() must return AbstractFallbackSelection"
                )
            return result
        return _legacy_fallback_selection(self.fallback.abstract(doi))

    def _fallback_selections_many(
        self,
        dois: tuple[str, ...],
    ) -> Mapping[str, AbstractFallbackSelection]:
        if self.fallback is None or not dois:
            return {}
        select_many = getattr(self.fallback, "select_many", None)
        if callable(select_many):
            result = select_many(dois)
            if not isinstance(result, Mapping):
                raise TypeError(
                    "fallback select_many() must return a mapping"
                )
            invalid = [
                doi
                for doi, selection in result.items()
                if not isinstance(doi, str)
                or not isinstance(selection, AbstractFallbackSelection)
            ]
            if invalid:
                raise TypeError(
                    "fallback select_many() must map DOI strings to "
                    "AbstractFallbackSelection"
                )
            return result
        return {
            doi: _legacy_fallback_selection(value)
            for doi, value in self.fallback.abstract_many(dois).items()
        }

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
        """Return collection enrichment for multiple DOI-backed work records."""
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
                default_source="publisher",
            )
            prepared[doi] = (base, extra)
            if not (extra.abstract or base.abstract).strip():
                fallback_dois.append(doi)

        fallback_selections = self._fallback_selections_many(
            tuple(fallback_dois)
        )

        result: dict[str, Enrichment] = {}
        for doi, (base, extra) in prepared.items():
            abstract = extra.abstract or base.abstract
            source = extra.abstract_source or base.abstract_source
            evidence = (
                tuple(base.abstract_evidence)
                + tuple(extra.abstract_evidence)
            )
            fallback = fallback_selections.get(
                doi,
                AbstractFallbackSelection(),
            )
            evidence += tuple(fallback.evidence)
            if not abstract.strip():
                abstract = fallback.abstract
                source = fallback.source if abstract else ""

            result[doi] = _clean_enrichment(
                Enrichment(
                    abstract=abstract,
                    keywords=extra.keywords or base.keywords,
                    event=extra.event,
                    abstract_source=source,
                    abstract_evidence=evidence,
                ),
                reporter=self.reporter,
                context=f"Fallback abstract for {doi}",
                default_source=source or "fallback",
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
            preserve_refused=discovery,
        )
        extra = (
            self.publisher.enrich(doi)
            if self.publisher is not None
            else Enrichment()
        )
        if not isinstance(extra, Enrichment):
            raise TypeError("publisher enrichment provider must return Enrichment")
        extra = _clean_enrichment(
            extra,
            reporter=self.reporter,
            context=f"Publisher abstract for {doi}",
            default_source="publisher",
            preserve_refused=discovery,
        )

        evidence = (
            tuple(base.abstract_evidence)
            + tuple(extra.abstract_evidence)
        )
        if discovery:
            abstract = " ".join(
                part for part in (base.abstract, extra.abstract) if part
            )
            source = "discovery"
            keywords = base.keywords + extra.keywords
        else:
            abstract = extra.abstract or base.abstract
            source = extra.abstract_source or base.abstract_source
            keywords = extra.keywords or base.keywords

        if not abstract.strip() and self.fallback is not None:
            fallback = self._fallback_selection(doi)
            abstract = fallback.abstract
            source = fallback.source if abstract else ""
            evidence += tuple(fallback.evidence)

        return _clean_enrichment(
            Enrichment(
                abstract=abstract,
                keywords=keywords,
                event=extra.event,
                abstract_source=source,
                abstract_evidence=evidence,
            ),
            reporter=self.reporter,
            context=f"Fallback abstract for {doi}",
            default_source=source or "fallback",
            preserve_refused=discovery,
        )
