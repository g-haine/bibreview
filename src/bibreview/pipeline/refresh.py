"""Detect stale publications and derive safe, human-reviewed refresh proposals.

Remote BibTeX is only a staleness detector. A refresh never returns a complete
replacement publication: configured missing fields may become explicit proposals,
while every other meaningful recollection difference is retained as collateral
review evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..model import Publication
from ..reporting import Reporter
from ..text import is_missing_metadata_value
from .audit import AuditValue, classify_audit_pair, publication_audit_record
from .backfill import BackfillCandidate
from .collect import (
    BibtexLookup,
    CitationLookup,
    EnrichmentLookup,
    WorkProvider,
    build_publication,
    publication_enrichment,
)


StoredBibtexLookup = Callable[[Publication], str | None]


@dataclass(frozen=True)
class RefreshDifference:
    """One meaningful collateral recollection difference that refresh will not apply."""

    publication_id: str
    doi: str
    title: str
    field: str
    classification: str
    current_value: AuditValue
    proposed_value: AuditValue


@dataclass(frozen=True)
class RefreshedItem:
    """One stale publication projected into safe proposals and collateral evidence."""

    publication_id: str
    doi: str
    title: str
    reason: str
    proposals: tuple[BackfillCandidate, ...]
    collateral: tuple[RefreshDifference, ...]


@dataclass(frozen=True)
class RefreshResult:
    """Complete read-only result of one safe refresh scan."""

    scanned_count: int
    eligible_count: int
    candidates: tuple[str, ...]
    items: tuple[RefreshedItem, ...]
    unavailable: tuple[str, ...]

    @property
    def proposals(self) -> tuple[BackfillCandidate, ...]:
        return tuple(
            proposal
            for item in self.items
            for proposal in item.proposals
        )

    @property
    def collateral(self) -> tuple[RefreshDifference, ...]:
        return tuple(
            difference
            for item in self.items
            for difference in item.collateral
        )


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
    """Detect stale records without creating automatic replacement publications.

    A publication is eligible only when its type is configured and at least one
    configured canonical scalar field is empty. Remote BibTeX is compared with
    the tracked BibTeX solely to decide whether recollection is warranted.

    Recollection values for configured fields that are currently empty become
    explicit human-review proposals. Meaningful differences affecting any other
    field are retained as collateral evidence and can never be promoted by the
    refresh workflow.
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
        if not any(
            is_missing_metadata_value(field, getattr(publication, field))
            for field in missing_fields
        ):
            continue
        eligible_count += 1
        doi = publication.doi
        if doi is None:
            progress.detail(
                f"{publication.id}: refresh skipped because no DOI is available"
            )
            continue
        if not publication.permalink:
            raise ValueError(f"{doi}: refresh requires an existing permalink")

        stored = stored_bibtex_lookup(publication)
        if stored is not None and not isinstance(stored, str):
            raise TypeError("stored BibTeX lookup must return a string or None")
        current_bibtex = bibtex_lookup(doi)
        if not isinstance(current_bibtex, str):
            raise TypeError("BibTeX lookup must return a string")
        if not current_bibtex.strip():
            progress.detail(
                f"{doi}: current BibTeX unavailable; existing state retained"
            )
            continue
        if stored is not None and stored == current_bibtex:
            continue

        reason = "missing BibTeX" if stored is None else "changed BibTeX"
        candidates.append(doi)
        progress.detail(f"{doi}: refresh candidate ({reason})")

        message = provider.work(doi)
        if message is None:
            unavailable.append(doi)
            progress.detail(
                f"{doi}: metadata unavailable; existing publication retained"
            )
            continue
        if not isinstance(message, Mapping):
            raise ValueError(
                f"{doi}: metadata provider returned a non-mapping work record"
            )

        resolved_enrichment = publication_enrichment(
            doi,
            message,
            enrichment_lookup=enrichment_lookup,
        )
        recollected = build_publication(
            doi,
            message,
            publication.permalink,
            citation_lookup=citation_lookup,
            enrichment=resolved_enrichment,
        )
        current_fields = publication_audit_record(publication).fields
        proposed_fields = publication_audit_record(recollected).fields

        proposals: list[BackfillCandidate] = []
        collateral: list[RefreshDifference] = []
        for field, current_value in current_fields.items():
            proposed_value = proposed_fields[field]

            if (
                field == "abstract"
                and field in missing_fields
                and isinstance(current_value, str)
                and is_missing_metadata_value(field, current_value)
            ):
                evidence = tuple(resolved_enrichment.abstract_evidence)
                if (
                    isinstance(proposed_value, str)
                    and not is_missing_metadata_value(field, proposed_value)
                ):
                    proposals.append(
                        BackfillCandidate(
                            publication_id=publication.id,
                            doi=doi,
                            title=publication.title,
                            field=field,
                            proposed_value=proposed_value,
                            evidence=evidence,
                        )
                    )
                    continue
                if evidence:
                    proposals.append(
                        BackfillCandidate(
                            publication_id=publication.id,
                            doi=doi,
                            title=publication.title,
                            field=field,
                            proposed_value="",
                            review_required=True,
                            evidence=evidence,
                        )
                    )
                    continue

            classification = classify_audit_pair(
                field,
                current_value,
                proposed_value,
            )
            if classification in {"equal", "formatting-only"}:
                continue

            safe_missing_proposal = (
                field in missing_fields
                and isinstance(current_value, str)
                and isinstance(proposed_value, str)
                and is_missing_metadata_value(field, current_value)
                and not is_missing_metadata_value(field, proposed_value)
            )
            if safe_missing_proposal:
                proposals.append(
                    BackfillCandidate(
                        publication_id=publication.id,
                        doi=doi,
                        title=publication.title,
                        field=field,
                        proposed_value=proposed_value,
                    )
                )
                continue

            collateral.append(
                RefreshDifference(
                    publication_id=publication.id,
                    doi=doi,
                    title=publication.title,
                    field=field,
                    classification=classification,
                    current_value=current_value,
                    proposed_value=proposed_value,
                )
            )

        items.append(
            RefreshedItem(
                publication_id=publication.id,
                doi=doi,
                title=publication.title,
                reason=reason,
                proposals=tuple(proposals),
                collateral=tuple(collateral),
            )
        )

    return RefreshResult(
        scanned_count=len(publication_values),
        eligible_count=eligible_count,
        candidates=tuple(candidates),
        items=tuple(items),
        unavailable=tuple(unavailable),
    )
