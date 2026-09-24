"""Provider-independent DOI discovery and configurable relevance screening."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Callable, Protocol

from ..identity import normalize_doi
from ..providers.base import Enrichment
from ..reporting import Reporter
from .enrich import crossref_enrichment


DEFAULT_ACCEPTED_TYPES = (
    "journal-article",
    "proceedings-article",
    "book-chapter",
    "book",
    "monograph",
)


class WorkProvider(Protocol):
    """Minimal metadata lookup required during discovery."""

    def work(self, doi: str) -> Mapping[str, Any] | None:
        """Return metadata for one DOI, or ``None`` when unavailable."""


EnrichmentLookup = Callable[[str, Mapping[str, Any]], Enrichment]


@dataclass(frozen=True)
class DiscoveryResult:
    """Read-only outcome of one discovery/relevance pass."""

    candidates: tuple[str, ...]
    queued: tuple[str, ...]
    review: tuple[str, ...]
    rejected: tuple[str, ...]
    skipped: tuple[str, ...]

    @property
    def screened_count(self) -> int:
        return len(self.queued) + len(self.review) + len(self.rejected)


def _normalized_text(value: str) -> str:
    """Normalize Unicode dash punctuation before configurable regex matching."""
    return "".join("-" if unicodedata.category(character) == "Pd" else character for character in value)


def is_relevant(value: str, patterns: Iterable[str]) -> bool:
    """Return whether any configured case-insensitive regular expression matches."""
    text = _normalized_text(value)
    return any(re.search(pattern, text, re.IGNORECASE) is not None for pattern in patterns)


def _title(message: Mapping[str, Any]) -> str:
    raw = message.get("title")
    if isinstance(raw, list):
        return str(raw[0]) if raw else ""
    return str(raw or "")


def discover(
    candidates: Iterable[str],
    *,
    provider: WorkProvider,
    known: Iterable[str] = (),
    rejected: Iterable[str] = (),
    patterns: Iterable[str] = (),
    unmatched: str = "manual-review",
    accepted_types: Iterable[str] = DEFAULT_ACCEPTED_TYPES,
    excluded_doi_substrings: Iterable[str] = (),
    enrichment_lookup: EnrichmentLookup | None = None,
    reporter: Reporter | None = None,
) -> DiscoveryResult:
    """Screen discovered DOI candidates without mutating project state.

    Candidates already present in known/rejected state are skipped. New DOI
    metadata is validated through the injected work provider. Unsupported or
    unavailable works are rejected. Relevant works are queued; unmatched works
    either go to manual review or are rejected according to project policy.
    """
    if unmatched not in {"manual-review", "reject"}:
        raise ValueError("unmatched policy must be 'manual-review' or 'reject'")

    progress = reporter or Reporter(-1)
    known_set = {normalize_doi(value) for value in known}
    rejected_set = {normalize_doi(value) for value in rejected}
    accepted = {str(value).strip() for value in accepted_types if str(value).strip()}
    excluded = tuple(
        str(value).strip().lower()
        for value in excluded_doi_substrings
        if str(value).strip()
    )
    relevance_patterns = tuple(patterns)

    unique: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        doi = normalize_doi(value)
        if doi not in seen:
            seen.add(doi)
            unique.append(doi)

    queued: list[str] = []
    review: list[str] = []
    newly_rejected: list[str] = []
    skipped: list[str] = []

    for index, doi in enumerate(unique, 1):
        if (
            doi in known_set
            or doi in rejected_set
            or any(fragment in doi for fragment in excluded)
        ):
            skipped.append(doi)
            progress.detail(f"[{index}/{len(unique)}] skipped known/excluded {doi}")
            continue

        progress.detail(f"[{index}/{len(unique)}] verifying {doi}")
        message = provider.work(doi)
        work_type = message.get("type") if message is not None else None
        if message is None or work_type not in accepted:
            newly_rejected.append(doi)
            reason = "absent from metadata provider" if message is None else f"unsupported type {work_type}"
            progress.detail(f"{doi}: rejected ({reason})")
            continue

        enrichment = (
            enrichment_lookup(doi, message)
            if enrichment_lookup is not None
            else crossref_enrichment(message, preserve_refused=True)
        )
        if not isinstance(enrichment, Enrichment):
            raise TypeError("discovery enrichment lookup must return Enrichment")
        text = " ".join(
            part
            for part in (
                _title(message),
                enrichment.abstract,
                " ".join(enrichment.keywords),
            )
            if part
        )
        if is_relevant(text, relevance_patterns):
            queued.append(doi)
            progress.detail(f"{doi}: queued for collection")
        elif unmatched == "manual-review":
            review.append(doi)
            progress.detail(f"{doi}: queued for manual relevance check")
        else:
            newly_rejected.append(doi)
            progress.detail(f"{doi}: rejected by relevance policy")

    return DiscoveryResult(
        candidates=tuple(unique),
        queued=tuple(queued),
        review=tuple(review),
        rejected=tuple(newly_rejected),
        skipped=tuple(skipped),
    )
