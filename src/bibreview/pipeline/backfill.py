"""Propose selected missing canonical scalar fields without replacing reviewed metadata."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..model import Publication
from ..providers.base import Enrichment
from ..providers.http import HttpError
from ..reporting import Reporter
from ..text import is_missing_metadata_value
from .collect import (
    BatchWorkProvider,
    EnrichmentLookup,
    EnrichmentManyLookup,
    WorkProvider,
    scalar_metadata_values,
)


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


def _individual_work(
    provider: WorkProvider,
    doi: str,
    *,
    reporter: Reporter,
) -> Mapping[str, Any] | None:
    """Fetch one work record without letting one DOI abort the whole pass."""
    try:
        message = provider.work(doi)
    except (HttpError, OSError, ValueError, TypeError):
        reporter.warning(
            f"{doi}: metadata lookup failed; marking this DOI unavailable "
            "for the current backfill pass."
        )
        return None
    if message is None:
        return None
    if not isinstance(message, Mapping):
        reporter.warning(
            f"{doi}: metadata provider returned a non-mapping work record; "
            "marking this DOI unavailable for the current backfill pass."
        )
        return None
    return message


def _prefetch_work_messages(
    dois: tuple[str, ...],
    *,
    provider: WorkProvider,
    batch_provider: BatchWorkProvider | None,
    reporter: Reporter,
) -> dict[str, Mapping[str, Any] | None]:
    """Fetch exact work records in bounded batches with per-DOI fallback."""
    if not dois:
        return {}

    if batch_provider is None:
        return {
            doi: _individual_work(provider, doi, reporter=reporter)
            for doi in dois
        }

    size = getattr(batch_provider, "BATCH_SIZE", 0)
    works = getattr(batch_provider, "works", None)
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size < 1
        or not callable(works)
    ):
        raise ValueError(
            "batch work provider must expose a positive BATCH_SIZE and works()"
        )

    result: dict[str, Mapping[str, Any] | None] = {}
    for offset in range(0, len(dois), size):
        chunk = dois[offset : offset + size]
        try:
            batch = works(chunk)
            if not isinstance(batch, Mapping):
                raise TypeError("batch work lookup must return a mapping")
        except (HttpError, OSError, ValueError, TypeError):
            reporter.warning(
                f"Metadata batch of {len(chunk)} DOI values failed; "
                "falling back to individual lookups for that chunk."
            )
            for doi in chunk:
                result[doi] = _individual_work(
                    provider,
                    doi,
                    reporter=reporter,
                )
            continue

        for doi in chunk:
            message = batch.get(doi)
            if isinstance(message, Mapping):
                result[doi] = message
                continue
            if message is not None:
                reporter.warning(
                    f"{doi}: batch metadata returned a non-mapping work record; "
                    "retrying that DOI individually."
                )
            result[doi] = _individual_work(
                provider,
                doi,
                reporter=reporter,
            )
    return result


def backfill(
    publications: Iterable[Publication],
    *,
    provider: WorkProvider,
    fields: Iterable[str],
    types: Iterable[str] = (),
    enrichment_lookup: EnrichmentLookup | None = None,
    batch_provider: BatchWorkProvider | None = None,
    enrichment_many_lookup: EnrichmentManyLookup | None = None,
    reporter: Reporter | None = None,
) -> BackfillResult:
    """Propose values only for requested semantically-missing scalar fields.

    Existing meaningful canonical values are never proposed for replacement.
    The abstract placeholder "Not Available" is treated as missing.
    When *types* is empty, all publication types are eligible.

    When batch collaborators are supplied, exact CrossRef-style work records
    and optional enrichment are prefetched without changing proposal ordering
    or human-review semantics.
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

    eligible: list[tuple[Publication, tuple[str, ...]]] = []
    for publication in publication_values:
        if selected_types and publication.type not in selected_types:
            continue
        missing = tuple(
            field
            for field in requested_fields
            if is_missing_metadata_value(field, getattr(publication, field))
        )
        if not missing or publication.doi is None:
            continue
        eligible.append((publication, missing))
        progress.detail(
            f"{publication.doi}: backfill candidate ({', '.join(missing)})"
        )

    dois = tuple(publication.doi for publication, _ in eligible)
    messages = _prefetch_work_messages(
        dois,
        provider=provider,
        batch_provider=batch_provider,
        reporter=progress,
    )

    precomputed_enrichment: Mapping[str, Enrichment] = {}
    if enrichment_many_lookup is not None:
        enrichment_messages: dict[str, Mapping[str, Any]] = {
            publication.doi: message
            for publication, missing in eligible
            if {"abstract", "event"} & set(missing)
            for message in (messages.get(publication.doi),)
            if message is not None
        }
        if enrichment_messages:
            try:
                batch_enrichment = enrichment_many_lookup(enrichment_messages)
                if not isinstance(batch_enrichment, Mapping):
                    raise TypeError(
                        "batched enrichment lookup must return a mapping"
                    )
                invalid = [
                    doi
                    for doi, value in batch_enrichment.items()
                    if not isinstance(doi, str)
                    or not isinstance(value, Enrichment)
                ]
                if invalid:
                    raise TypeError(
                        "batched enrichment lookup must map DOI strings to Enrichment"
                    )
                precomputed_enrichment = batch_enrichment
            except (HttpError, OSError, ValueError, TypeError):
                progress.warning(
                    "Batched enrichment failed; falling back to individual "
                    "enrichment lookups for the affected DOI values."
                )

    def cached_enrichment(
        doi: str,
        message: Mapping[str, Any],
    ) -> Enrichment:
        cached = precomputed_enrichment.get(doi)
        if isinstance(cached, Enrichment):
            return cached
        if enrichment_lookup is None:
            return Enrichment()
        return enrichment_lookup(doi, message)

    for publication, missing in eligible:
        doi = publication.doi
        assert doi is not None
        message = messages.get(doi)
        if message is None:
            unavailable.append(doi)
            continue

        proposed = scalar_metadata_values(
            doi,
            message,
            missing,
            enrichment_lookup=(
                cached_enrichment
                if {"abstract", "event"} & set(missing)
                else None
            ),
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
        eligible_count=len(eligible),
        candidates=tuple(candidates),
        unavailable=tuple(unavailable),
        no_value=tuple(no_value),
    )
