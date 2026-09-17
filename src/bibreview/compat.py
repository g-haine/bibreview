"""Compatibility adapter for the pre-BibReview bibliography JSON schema.

This module is deliberately isolated from the canonical model. It exists so the
first BibReview migrations can read and reproduce established project data
without making legacy field names part of the long-term API.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .identity import migration_id_from_doi, normalize_doi, validate_publication_id
from .model import Author, Publication, Reference
from .storage import read_json, write_json


class CompatibilityError(ValueError):
    """Raised when a legacy bibliography record cannot be migrated safely."""


@dataclass(frozen=True)
class LegacyPublication:
    """Canonical publication plus its lossless legacy compatibility envelope."""

    publication: Publication
    source_record: Mapping[str, Any]


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _legacy_doi(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return normalize_doi(text)


def _legacy_reference_doi(value: Any) -> str | None:
    """Return a canonical reference DOI, ignoring malformed legacy values.

    Reference metadata is secondary and the raw source value remains preserved
    in ``source_record`` for lossless legacy round-tripping. A malformed
    reference DOI therefore must not prevent migration of an otherwise valid
    publication and must not become a canonical BibReview identifier.
    """
    try:
        return _legacy_doi(value)
    except ValueError:
        return None


def _authors(value: Any) -> tuple[Author, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CompatibilityError("legacy authors must be a list of objects or null")
    result = []
    for item in value:
        given = item.get("given")
        family = item.get("family")
        if not ((isinstance(given, str) and given.strip()) or (isinstance(family, str) and family.strip())):
            raise CompatibilityError("legacy author needs a non-empty given or family name")
        result.append(Author(given=given if isinstance(given, str) else None,
                             family=family if isinstance(family, str) else None))
    return tuple(result)


def _keywords(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(item.strip() for item in value if item.strip())
    raise CompatibilityError("legacy keywords must be a string, list of strings, or null")


def _created_date(record: Mapping[str, Any]) -> date | None:
    values = [record.get("dateY"), record.get("dateM"), record.get("dateD")]
    if all(value in (None, "") for value in values):
        return None
    try:
        return date(*(int(value) for value in values))
    except (TypeError, ValueError) as error:
        raise CompatibilityError("legacy record has an invalid creation date") from error


def _references(value: Any) -> tuple[Reference, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CompatibilityError("legacy references must be a list of objects or null")
    result = []
    for item in value:
        identifiers: dict[str, str] = {}
        doi = _legacy_reference_doi(item.get("doi"))
        if doi is not None:
            identifiers["doi"] = doi
        result.append(Reference(identifiers=identifiers, citation=_text(item.get("title"))))
    return tuple(result)


def legacy_record_to_publication(record: Mapping[str, Any]) -> LegacyPublication:
    """Convert one legacy record without discarding fields needed for round-tripping."""
    if not isinstance(record, Mapping):
        raise CompatibilityError("legacy bibliography record must be an object")

    raw_doi = record.get("doi")
    doi = _legacy_doi(raw_doi)
    raw_id = record.get("id")
    if raw_id not in (None, ""):
        publication_id = validate_publication_id(str(raw_id))
    elif doi is not None:
        publication_id = migration_id_from_doi(doi)
    else:
        raise CompatibilityError(
            "legacy record without an internal id or DOI cannot receive a reproducible migration id"
        )

    identifiers = {"doi": doi} if doi is not None else {}
    publication = Publication(
        id=publication_id,
        identifiers=identifiers,
        type=_text(record.get("type")),
        title=_text(record.get("title")),
        authors=_authors(record.get("authors")),
        abstract=_text(record.get("abstract")),
        container_title=_text(record.get("journal")),
        publication_year=_text(record.get("year")),
        volume=_text(record.get("volume")),
        issue=_text(record.get("issue")),
        pages=_text(record.get("pages")),
        publisher=_text(record.get("publisher")),
        event=_text(record.get("event")),
        keywords=_keywords(record.get("keywords")),
        created_date=_created_date(record),
        permalink=_text(record.get("permalink")),
        references=_references(record.get("references")),
    )
    return LegacyPublication(publication=publication, source_record=deepcopy(dict(record)))


def _source_author_projection(value: Any) -> tuple[tuple[str | None, str | None], ...] | None:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    return tuple((item.get("given") if isinstance(item.get("given"), str) else None,
                  item.get("family") if isinstance(item.get("family"), str) else None)
                 for item in value)


def _source_reference_projection(value: Any) -> tuple[tuple[str | None, str], ...] | None:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    return tuple((_legacy_reference_doi(item.get("doi")), _text(item.get("title"))) for item in value)


def publication_to_legacy(
    item: LegacyPublication,
    *,
    include_internal_id: bool = False,
) -> dict[str, Any]:
    """Render one compatibility envelope back to the legacy schema losslessly.

    Unknown/source-specific fields are preserved. Canonical fields are updated
    from ``Publication``. The internal UUID is omitted by default so a pure
    read/round-trip does not alter an established legacy file.
    """
    publication = item.publication
    result = deepcopy(dict(item.source_record))

    if include_internal_id:
        result["id"] = publication.id
    else:
        result.pop("id", None)

    result["doi"] = publication.doi
    result["type"] = publication.type
    result["title"] = publication.title
    result["abstract"] = publication.abstract
    result["journal"] = publication.container_title
    result["year"] = publication.publication_year
    result["volume"] = publication.volume
    result["issue"] = publication.issue
    result["pages"] = publication.pages
    result["publisher"] = publication.publisher
    result["event"] = publication.event
    result["permalink"] = publication.permalink

    source_authors = _source_author_projection(result.get("authors"))
    canonical_authors = tuple((author.given, author.family) for author in publication.authors)
    if source_authors != canonical_authors:
        result["authors"] = [
            {key: value for key, value in (("given", author.given), ("family", author.family))
             if value is not None}
            for author in publication.authors
        ]

    source_keywords = _keywords(result.get("keywords"))
    if source_keywords != publication.keywords:
        result["keywords"] = ", ".join(publication.keywords)

    source_references = _source_reference_projection(result.get("references"))
    canonical_references = tuple((reference.identifiers.get("doi"), reference.citation)
                                 for reference in publication.references)
    if source_references != canonical_references:
        result["references"] = [
            {
                "doi": reference.identifiers.get("doi") or "null",
                "title": reference.citation,
            }
            for reference in publication.references
        ]

    if publication.created_date is None:
        result.pop("dateY", None)
        result.pop("dateM", None)
        result.pop("dateD", None)
    else:
        result["dateY"] = str(publication.created_date.year)
        result["dateM"] = str(publication.created_date.month)
        result["dateD"] = str(publication.created_date.day)

    return result


def load_legacy_bibliography(path: Path | str) -> tuple[LegacyPublication, ...]:
    """Read a legacy bibliography without mutating it."""
    records = read_json(path, list)
    result = []
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise CompatibilityError(f"record {index} must be an object")
        try:
            result.append(legacy_record_to_publication(record))
        except (CompatibilityError, ValueError) as error:
            raise CompatibilityError(f"record {index}: {error}") from error
    return tuple(result)


def legacy_records(
    items: tuple[LegacyPublication, ...] | list[LegacyPublication],
    *,
    include_internal_ids: bool = False,
) -> list[dict[str, Any]]:
    """Serialize compatibility envelopes without touching the filesystem."""
    return [publication_to_legacy(item, include_internal_id=include_internal_ids) for item in items]


def write_legacy_bibliography(
    path: Path | str,
    items: tuple[LegacyPublication, ...] | list[LegacyPublication],
    *,
    include_internal_ids: bool = False,
) -> None:
    """Atomically write the legacy schema when an explicit migration step requests it."""
    write_json(path, legacy_records(items, include_internal_ids=include_internal_ids))
