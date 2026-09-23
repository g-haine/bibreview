"""Fill selected missing canonical scalar fields without replacing reviewed metadata."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from ..model import Publication
from ..reporting import Reporter
from .collect import EnrichmentLookup, WorkProvider, build_publication


BACKFILL_FIELDS = frozenset({
    "title",
    "abstract",
    "container_title",
    "publication_year",
    "volume",
    "issue",
    "pages",
    "publisher",
    "event",
})


@dataclass(frozen=True)
class BackfillItem:
    """One canonical publication with selected previously-empty fields filled."""

    publication: Publication
    fields: tuple[str, ...]


@dataclass(frozen=True)
class BackfillResult:
    """Complete read-only result of one missing-field backfill pass."""

    scanned_count: int
    eligible_count: int
    items: tuple[BackfillItem, ...]
    unavailable: tuple[str, ...]
    no_value: tuple[str, ...]


def backfill(
    publications: Iterable[Publication],
    *,
    provider: WorkProvider,
    fields: Iterable[str],
    types: Iterable[str] = (),
    enrichment_lookup: EnrichmentLookup | None = None,
    reporter: Reporter | None = None,
) -> BackfillResult:
    """Fill only requested empty scalar fields on existing DOI-backed records.

    Existing non-empty canonical values are never replaced. When *types* is
    empty, all publication types are eligible.
    """
    publication_values = tuple(publications)
    requested_fields = tuple(dict.fromkeys(fields))
    if not requested_fields:
        raise ValueError("backfill requires at least one field")
    unsupported = set(requested_fields) - BACKFILL_FIELDS
    if unsupported:
        raise ValueError(
            "unsupported backfill field(s): " + ", ".join(sorted(unsupported))
        )

    selected_types = set(types)
    progress = reporter or Reporter(-1)
    items: list[BackfillItem] = []
    unavailable: list[str] = []
    no_value: list[str] = []
    eligible_count = 0

    for publication in publication_values:
        if selected_types and publication.type not in selected_types:
            continue
        missing = tuple(
            field
            for field in requested_fields
            if not getattr(publication, field)
        )
        if not missing:
            continue
        doi = publication.doi
        if doi is None:
            continue
        eligible_count += 1
        progress.detail(
            f"{doi}: backfill candidate ({', '.join(missing)})"
        )

        message = provider.work(doi)
        if message is None:
            unavailable.append(doi)
            continue

        candidate = build_publication(
            doi,
            message,
            publication.permalink,
            enrichment_lookup=enrichment_lookup,
        )
        updates = {
            field: getattr(candidate, field)
            for field in missing
            if getattr(candidate, field)
        }
        if not updates:
            no_value.append(doi)
            continue

        items.append(
            BackfillItem(
                publication=replace(publication, **updates),
                fields=tuple(field for field in missing if field in updates),
            )
        )

    return BackfillResult(
        scanned_count=len(publication_values),
        eligible_count=eligible_count,
        items=tuple(items),
        unavailable=tuple(unavailable),
        no_value=tuple(no_value),
    )
