"""Local CSL rendering for DOI-backed reference citations.

The formatter consumes transient CrossRef work metadata and returns only stable
citation strings. It does not create BibReview publications or references and
does not persist provider metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from io import BytesIO
from types import MappingProxyType
from typing import Any

from citeproc import (
    Citation,
    CitationItem,
    CitationStylesBibliography,
    CitationStylesStyle,
    formatter,
)
from citeproc.source.json import CiteProcJSON

from .identity import normalize_doi
from .providers.crossref import crossref_page_locator


_STYLE_RESOURCE = (
    "data/styles/springer-basic-author-date-no-et-al-with-issue.csl"
)

class CitationFormatError(ValueError):
    """Raised when transient provider metadata cannot be rendered safely."""


@dataclass(frozen=True)
class CitationBatchResult:
    """Locally rendered DOI citations plus per-DOI formatting failures."""

    citations: Mapping[str, str]
    errors: Mapping[str, str]


_CROSSREF_TO_CSL_TYPE = {
    "journal-article": "article-journal",
    "proceedings-article": "paper-conference",
    "book-chapter": "chapter",
    "book-section": "chapter",
    "book-part": "chapter",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "reference-book": "book",
    "proceedings": "book",
    "report": "report",
    "report-series": "report",
    "dissertation": "thesis",
    "posted-content": "article",
    "peer-review": "article",
    "component": "article",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _first(value: Any) -> str:
    if isinstance(value, list) and value:
        first = value[0]
        return _text(first)
    if isinstance(value, str):
        return value.strip()
    return ""


def _names(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        given = _text(item.get("given"))
        family = _text(item.get("family"))
        literal = _text(item.get("name"))
        if not family and literal:
            family = literal
        name: dict[str, str] = {}
        if given:
            name["given"] = given
        if family:
            name["family"] = family
        suffix = _text(item.get("suffix"))
        if suffix:
            name["suffix"] = suffix
        if name:
            result.append(name)
    return result


def _issued(message: Mapping[str, Any]) -> dict[str, list[list[int]]]:
    for field in (
        "issued",
        "published-print",
        "published-online",
        "published",
        "created",
    ):
        raw = message.get(field)
        if not isinstance(raw, Mapping):
            continue
        parts = raw.get("date-parts")
        if (
            not isinstance(parts, list)
            or not parts
            or not isinstance(parts[0], list)
            or not parts[0]
        ):
            continue
        cleaned: list[int] = []
        for value in parts[0][:3]:
            if isinstance(value, int) and not isinstance(value, bool):
                cleaned.append(value)
            elif isinstance(value, str) and value.isdigit():
                cleaned.append(int(value))
            else:
                break
        if cleaned:
            return {"date-parts": [cleaned]}
    return {}


def crossref_work_to_csl(
    doi: str,
    message: Mapping[str, Any],
) -> dict[str, Any]:
    """Convert one transient CrossRef work record to CSL JSON."""
    normalized = normalize_doi(doi)
    raw_type = str(message.get("type", "")).strip()
    item: dict[str, Any] = {
        "id": normalized,
        "type": _CROSSREF_TO_CSL_TYPE.get(raw_type, "article"),
        "DOI": normalized,
    }

    scalar_fields = {
        "title": _first(message.get("title")),
        "container-title": _first(message.get("container-title")),
        "container-title-short": _first(message.get("short-container-title")),
        "volume": _text(message.get("volume")),
        "issue": _text(message.get("issue")),
        "page": crossref_page_locator(message),
        "publisher": _text(message.get("publisher")),
        "publisher-place": _text(message.get("publisher-location")),
        "edition": _text(message.get("edition")),
        "URL": _text(message.get("URL")),
    }
    item.update(
        {
            key: value
            for key, value in scalar_fields.items()
            if value
        }
    )

    authors = _names(message.get("author"))
    if authors:
        item["author"] = authors
    editors = _names(message.get("editor"))
    if editors:
        item["editor"] = editors
    issued = _issued(message)
    if issued:
        item["issued"] = issued
    return item


def _style_bytes() -> bytes:
    return (
        resources.files("bibreview")
        .joinpath(_STYLE_RESOURCE)
        .read_bytes()
    )


def format_crossref_citations(
    messages: Mapping[str, Mapping[str, Any]],
) -> CitationBatchResult:
    """Render one DOI batch with a single local CSL style load."""
    prepared: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for raw_doi, message in messages.items():
        doi = normalize_doi(raw_doi)
        if not isinstance(message, Mapping):
            errors[doi] = "CrossRef citation metadata must be a mapping"
            continue
        try:
            prepared[doi] = crossref_work_to_csl(doi, message)
        except (TypeError, ValueError, KeyError) as error:
            errors[doi] = str(error)

    if not prepared:
        return CitationBatchResult(
            citations=MappingProxyType({}),
            errors=MappingProxyType(errors),
        )

    try:
        style = CitationStylesStyle(BytesIO(_style_bytes()), validate=False)
        source = CiteProcJSON(list(prepared.values()))
    except Exception as error:
        raise CitationFormatError(
            f"CSL citation batch initialization failed: {error}"
        ) from error

    citations: dict[str, str] = {}
    for doi in prepared:
        try:
            bibliography = CitationStylesBibliography(
                style,
                source,
                formatter.plain,
            )
            bibliography.register(Citation([CitationItem(doi)]))
            entries = bibliography.bibliography()
            citation = (
                "".join(str(part) for part in entries[0]).strip()
                if entries
                else ""
            )
        except Exception as error:
            errors[doi] = f"CSL citation rendering failed: {error}"
            continue
        if citation:
            citations[doi] = citation
        else:
            errors[doi] = "CSL citation rendering returned an empty value"

    return CitationBatchResult(
        citations=MappingProxyType(citations),
        errors=MappingProxyType(errors),
    )


def format_crossref_citation(
    doi: str,
    message: Mapping[str, Any],
) -> str:
    """Render one DOI through the same local batch formatter."""
    normalized = normalize_doi(doi)
    result = format_crossref_citations({normalized: message})
    citation = result.citations.get(normalized)
    if citation:
        return citation
    raise CitationFormatError(
        f"{normalized}: {result.errors.get(normalized, 'citation unavailable')}"
    )
