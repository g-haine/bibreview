"""Refresh existing DOI-backed publications without mutating project state.

Refresh is deliberately separate from discovery. It inspects already-known
publications selected by project policy, compares their stored BibTeX with the
current DOI rendering, and recollects only stale candidates into canonical
staging data. Persistent identity is preserved later by the merge layer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..model import Publication
from ..reporting import Reporter
from .collect import (
    BibtexLookup,
    CitationLookup,
    EnrichmentLookup,
    WorkProvider,
    build_publication,
)


StoredBibtexLookup = Callable[[Publication], str | None]


@dataclass(frozen=True)
class RefreshedItem:
    """One recollected publication and its current BibTeX representation."""

    publication: Publication
    bibtex: str
    reason: str


@dataclass(frozen=True)
class RefreshResult:
    """Complete read-only result of one refresh pass."""

    scanned_count: int
    eligible_count: int
    candidates: tuple[str, ...]
    items: tuple[RefreshedItem, ...]
    unavailable: tuple[str, ...]

    @property
    def publications(self) -> tuple[Publication, ...]:
        return tuple(item.publication for item in self.items)


def refresh(
    publications: Iterable[Publication],
    *,
    provider: WorkProvider,
    stored_bibtex_lookup: StoredBibtexLookup,
    bibtex_lookup: BibtexLookup,
    types: Iterable[str],
    when_missing_any: Iterable[str],
    enrichment_lookup: EnrichmentLookup | None = None,
    citation_lookup: CitationLookup | None = None,
    reporter: Reporter | None = None,
) -> RefreshResult:
    """Detect stale existing publications and recollect them into memory.

    A publication is eligible only when its type is configured and at least one
    configured canonical string field is empty. Eligible DOI-backed records are
    refreshed when the stored BibTeX is missing or differs byte-for-byte as text
    from the current DOI BibTeX. Existing permalinks are preserved deliberately.
    """
    publication_values = tuple(publications)
    selected_types = set(types)
    missing_fields = tuple(when_missing_any)
    progress = reporter or Reporter(-1)

    candidates: list[str] = []
    items: list[RefreshedItem] = []
    unavailable: list[str] = []
    eligible_count = 0

    if not selected_types or not missing_fields:
        return RefreshResult(
            scanned_count=len(publication_values),
            eligible_count=0,
            candidates=(),
            items=(),
            unavailable=(),
        )

    for publication in publication_values:
        if publication.type not in selected_types:
            continue
        if not any(not getattr(publication, field) for field in missing_fields):
            continue
        eligible_count += 1
        doi = publication.doi
        if doi is None:
            progress.detail(f"{publication.id}: refresh skipped because no DOI is available")
            continue
        if not publication.permalink:
            raise ValueError(f"{doi}: refresh requires an existing permalink")

        stored = stored_bibtex_lookup(publication)
        if stored is not None and not isinstance(stored, str):
            raise TypeError("stored BibTeX lookup must return a string or None")
        current = bibtex_lookup(doi)
        if not isinstance(current, str):
            raise TypeError("BibTeX lookup must return a string")
        if not current.strip():
            progress.detail(
                f"{doi}: current BibTeX unavailable; existing publication and BibTeX retained"
            )
            continue
        if stored is not None and stored == current:
            continue

        reason = "missing BibTeX" if stored is None else "changed BibTeX"
        candidates.append(doi)
        progress.detail(f"{doi}: refresh candidate ({reason})")
        message = provider.work(doi)
        if message is None:
            unavailable.append(doi)
            progress.detail(f"{doi}: metadata unavailable; existing publication retained")
            continue
        if not isinstance(message, Mapping):
            raise ValueError(f"{doi}: metadata provider returned a non-mapping work record")

        recollected = build_publication(
            doi,
            message,
            publication.permalink,
            enrichment_lookup=enrichment_lookup,
            citation_lookup=citation_lookup,
        )
        items.append(
            RefreshedItem(
                publication=recollected,
                bibtex=current,
                reason=reason,
            )
        )

    return RefreshResult(
        scanned_count=len(publication_values),
        eligible_count=eligible_count,
        candidates=tuple(candidates),
        items=tuple(items),
        unavailable=tuple(unavailable),
    )
