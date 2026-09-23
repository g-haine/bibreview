"""Propose selected missing canonical scalar fields without replacing reviewed metadata."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..model import Publication
from ..reporting import Reporter
from ..text import is_missing_metadata_value
from .collect import EnrichmentLookup, WorkProvider, scalar_metadata_values


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
class BackfillCandidate:
    """One proposed value for one currently-empty canonical field."""

    publication_id: str
    doi: str
    title: str
    field: str
    proposed_value: str

    @property
    def key(self) -> str:
        return f"{self.publication_id}:{self.field}"


@dataclass(frozen=True)
class BackfillResult:
    """Complete read-only result of one missing-field proposal pass."""

    scanned_count: int
    eligible_count: int
    candidates: tuple[BackfillCandidate, ...]
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
    """Propose values only for requested semantically-missing scalar fields.

    Existing meaningful canonical values are never proposed for replacement.
    The abstract placeholder "Not Available" is treated as missing.
    When *types* is empty, all publication types are eligible.
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
    candidates: list[BackfillCandidate] = []
    unavailable: list[str] = []
    no_value: list[str] = []
    eligible_count = 0

    for publication in publication_values:
        if selected_types and publication.type not in selected_types:
            continue
        missing = tuple(
            field
            for field in requested_fields
            if is_missing_metadata_value(field, getattr(publication, field))
        )
        if not missing:
            continue
        doi = publication.doi
        if doi is None:
            continue
        eligible_count += 1
        progress.detail(f"{doi}: backfill candidate ({', '.join(missing)})")

        message = provider.work(doi)
        if message is None:
            unavailable.append(doi)
            continue

        proposed = scalar_metadata_values(
            doi,
            message,
            missing,
            enrichment_lookup=enrichment_lookup,
        )
        found = False
        for field in missing:
            value = proposed[field]
            if is_missing_metadata_value(field, value):
                continue
            found = True
            candidates.append(
                BackfillCandidate(
                    publication_id=publication.id,
                    doi=doi,
                    title=publication.title,
                    field=field,
                    proposed_value=value,
                )
            )
        if not found:
            no_value.append(doi)

    return BackfillResult(
        scanned_count=len(publication_values),
        eligible_count=eligible_count,
        candidates=tuple(candidates),
        unavailable=tuple(unavailable),
        no_value=tuple(no_value),
    )
