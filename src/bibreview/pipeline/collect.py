"""Collect DOI-backed publications without mutating project files.

This module owns project-independent DOI collection decisions. Network adapters are injected, and persistence is
left to later storage/merge stages so failures cannot leave a partial on-disk
collection.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import re
from typing import Any, Protocol

from ..identity import IdentityError, new_publication_id, normalize_doi
from ..model import Author, Publication, Reference
from ..providers.base import Enrichment
from ..reporting import Reporter
from ..text import clean_metadata, safe_component, slugify


class WorkProvider(Protocol):
    """Minimal metadata-provider contract required by the collection pipeline."""

    def work(self, doi: str) -> dict | None:
        """Return one provider work record, or ``None`` when it is absent."""


@dataclass(frozen=True)
class CollectedItem:
    """One canonical publication plus optional rendered BibTeX."""

    publication: Publication
    bibtex: str | None = None


@dataclass(frozen=True)
class CollectionResult:
    """Complete read-only result of one collection pass."""

    submitted_count: int
    candidates: tuple[str, ...]
    items: tuple[CollectedItem, ...]
    unavailable: tuple[str, ...]

    @property
    def publications(self) -> tuple[Publication, ...]:
        return tuple(item.publication for item in self.items)


EnrichmentLookup = Callable[[str, Mapping[str, Any]], Enrichment]
CitationLookup = Callable[[str], str]
BibtexLookup = Callable[[str], str]

_MATHML = re.compile(r"<[^>]*mml[^>]*>")


def prepare_dois(submitted: Iterable[str], known: Iterable[str] = ()) -> tuple[str, ...]:
    """Normalize, de-duplicate and filter submitted DOI values while preserving order."""
    known_normalized: set[str] = set()
    for value in known:
        if isinstance(value, str) and not value.strip():
            continue
        known_normalized.add(normalize_doi(value))

    result: list[str] = []
    seen: set[str] = set()
    for value in submitted:
        if isinstance(value, str) and not value.strip():
            continue
        normalized = normalize_doi(value)
        if normalized in known_normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _first(value: Any) -> str:
    if isinstance(value, list) and value:
        return _string(value[0])
    return ""


def _created_date(message: Mapping[str, Any], doi: str) -> date:
    created = message.get("created")
    parts = created.get("date-parts") if isinstance(created, Mapping) else None
    first = parts[0] if isinstance(parts, list) and parts else None
    if not isinstance(first, list) or len(first) < 3:
        raise ValueError(f"{doi}: missing CrossRef creation date")
    try:
        return date(int(first[0]), int(first[1]), int(first[2]))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{doi}: invalid CrossRef creation date") from error


def _publication_year(message: Mapping[str, Any], created: date) -> str:
    published = message.get("published-print")
    parts = published.get("date-parts") if isinstance(published, Mapping) else None
    first = parts[0] if isinstance(parts, list) and parts else None
    if isinstance(first, list) and first:
        try:
            return str(int(first[0]))
        except (TypeError, ValueError):
            pass
    return str(created.year)


def _authors(value: Any) -> tuple[Author, ...]:
    if not isinstance(value, list):
        return ()
    result: list[Author] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        given = _string(item.get("given")).strip() or None
        family = _string(item.get("family")).strip() or None
        literal = _string(item.get("name")).strip() or None
        if given is None and family is None and literal is None:
            continue
        source_fields = deepcopy(
            {
                key: field_value
                for key, field_value in item.items()
                if key not in {"given", "family", "name"}
            }
        )
        result.append(
            Author(
                given=given,
                family=family,
                literal=literal,
                source_fields=source_fields,
            )
        )
    return tuple(result)


def _reference_citation(reference: Mapping[str, Any]) -> str:
    unstructured = _string(reference.get("unstructured")).strip()
    if unstructured:
        return clean_metadata(unstructured)
    values = [
        f"{reference['author']}," if reference.get("author") else "",
        f"{reference['article-title']}." if reference.get("article-title") else "",
        reference.get("journal-title"),
        reference.get("volume-title"),
        f"({reference['year']})" if reference.get("year") else "",
    ]
    return clean_metadata(" ".join(str(value) for value in values if value))


def _references(value: Any, citation_lookup: CitationLookup | None) -> tuple[Reference, ...]:
    if not isinstance(value, list):
        return ()
    result: list[Reference] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        identifiers: dict[str, str] = {}
        raw_doi = item.get("DOI")
        doi: str | None = None
        if raw_doi not in (None, ""):
            try:
                doi = normalize_doi(str(raw_doi))
            except IdentityError:
                doi = None
        if doi is not None:
            identifiers["doi"] = doi
        citation = citation_lookup(doi) if doi is not None and citation_lookup else _reference_citation(item)
        result.append(Reference(identifiers=identifiers, citation=clean_metadata(citation)))
    return tuple(result)


def _default_enrichment(message: Mapping[str, Any]) -> Enrichment:
    abstract = clean_metadata(_string(message.get("abstract")), abstract=True)
    subjects = message.get("subject")
    keywords = tuple(
        clean_metadata(_string(value))
        for value in subjects
        if _string(value).strip()
    ) if isinstance(subjects, list) else ()
    return Enrichment(abstract=abstract, keywords=keywords)


def build_publication(
    doi: str,
    message: Mapping[str, Any],
    slug: str,
    *,
    enrichment_lookup: EnrichmentLookup | None = None,
    citation_lookup: CitationLookup | None = None,
) -> Publication:
    """Build one canonical publication from a CrossRef work message."""
    normalized_doi = normalize_doi(doi)
    safe_component(slug)
    created = _created_date(message, normalized_doi)
    title = _MATHML.sub("", _first(message.get("title")))
    base_enrichment = _default_enrichment(message)
    if enrichment_lookup is None:
        enrichment = base_enrichment
    else:
        extra = enrichment_lookup(normalized_doi, message)
        if not isinstance(extra, Enrichment):
            raise TypeError("enrichment lookup must return Enrichment")
        enrichment = Enrichment(
            abstract=extra.abstract or base_enrichment.abstract,
            keywords=extra.keywords or base_enrichment.keywords,
            event=extra.event or base_enrichment.event,
        )

    identifiers: dict[str, str] = {"doi": normalized_doi}
    isbn_values = message.get("isbn-type")
    if isinstance(isbn_values, list) and isbn_values and isinstance(isbn_values[0], Mapping):
        isbn = _string(isbn_values[0].get("value")).strip()
        if isbn:
            identifiers["isbn"] = isbn

    return Publication(
        id=new_publication_id(),
        identifiers=identifiers,
        type=_string(message.get("type")),
        title=title,
        authors=_authors(message.get("author")),
        abstract=clean_metadata(enrichment.abstract, abstract=True).strip(),
        container_title=_first(message.get("container-title")),
        publication_year=_publication_year(message, created),
        volume=_string(message.get("volume")),
        issue=_string(message.get("issue")),
        pages=_string(message.get("page")).replace("-", "--"),
        publisher=_string(message.get("publisher")),
        event=clean_metadata(enrichment.event),
        keywords=tuple(clean_metadata(keyword) for keyword in enrichment.keywords if keyword.strip()),
        created_date=created,
        permalink=slug,
        references=_references(message.get("reference"), citation_lookup),
    )


def _slug_for_message(message: Mapping[str, Any], used: set[str]) -> str:
    title = _MATHML.sub("", _first(message.get("title")))
    slug = slugify(title)
    safe_component(slug)
    while slug in used:
        slug += "0"
    safe_component(slug)
    used.add(slug)
    return slug


def collect(
    submitted: Iterable[str],
    *,
    provider: WorkProvider,
    known: Iterable[str] = (),
    used_slugs: Iterable[str] = (),
    enrichment_lookup: EnrichmentLookup | None = None,
    citation_lookup: CitationLookup | None = None,
    bibtex_lookup: BibtexLookup | None = None,
    reporter: Reporter | None = None,
) -> CollectionResult:
    """Fetch and build new publications without writing project state."""
    submitted_values = tuple(submitted)
    candidates = prepare_dois(submitted_values, known)
    progress = reporter or Reporter(-1)
    progress.step(
        f"Prepared {len(candidates)} unique DOI(s) from {len(submitted_values)} input value(s)"
    )

    used = set(used_slugs)
    items: list[CollectedItem] = []
    unavailable: list[str] = []
    for index, doi in enumerate(candidates, 1):
        progress.detail(f"[{index}/{len(candidates)}] fetching {doi}")
        message = provider.work(doi)
        if message is None:
            unavailable.append(doi)
            progress.detail(f"{doi}: unavailable from metadata provider")
            continue
        if not isinstance(message, Mapping):
            raise ValueError(f"{doi}: metadata provider returned a non-mapping work record")
        slug = _slug_for_message(message, used)
        progress.detail(f"{doi}: permalink {slug}")
        publication = build_publication(
            doi,
            message,
            slug,
            enrichment_lookup=enrichment_lookup,
            citation_lookup=citation_lookup,
        )
        bibtex = bibtex_lookup(doi) if bibtex_lookup is not None else None
        if bibtex is not None and not isinstance(bibtex, str):
            raise TypeError("BibTeX lookup must return a string")
        items.append(CollectedItem(publication=publication, bibtex=bibtex))

    return CollectionResult(
        submitted_count=len(submitted_values),
        candidates=candidates,
        items=tuple(items),
        unavailable=tuple(unavailable),
    )
