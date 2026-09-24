"""Local CSL rendering for DOI-backed reference citations.

The formatter consumes transient CrossRef work metadata and returns only stable
citation strings. It does not create BibReview publications or references and
does not persist provider metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from io import BytesIO
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


def _first(value: Any) -> str:
    if isinstance(value, list) and value:
        first = value[0]
        return str(first).strip() if first is not None else ""
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
        given = str(item.get("given", "")).strip()
        family = str(item.get("family", "")).strip()
        literal = str(item.get("name", "")).strip()
        if not family and literal:
            family = literal
        name: dict[str, str] = {}
        if given:
            name["given"] = given
        if family:
            name["family"] = family
        suffix = str(item.get("suffix", "")).strip()
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
        "volume": str(message.get("volume", "")).strip(),
        "issue": str(message.get("issue", "")).strip(),
        "page": crossref_page_locator(message),
        "publisher": str(message.get("publisher", "")).strip(),
        "publisher-place": str(message.get("publisher-location", "")).strip(),
        "edition": str(message.get("edition", "")).strip(),
        "URL": str(message.get("URL", "")).strip(),
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


def _render_one(item: Mapping[str, Any], style_bytes: bytes) -> str:
    source = CiteProcJSON([dict(item)])
    style = CitationStylesStyle(BytesIO(style_bytes), validate=False)
    bibliography = CitationStylesBibliography(
        style,
        source,
        formatter.plain,
    )
    citation = Citation([CitationItem(str(item["id"]))])
    bibliography.register(citation)
    entries = bibliography.bibliography()
    if not entries:
        return ""
    return "".join(str(part) for part in entries[0]).strip()


def format_crossref_citation(
    doi: str,
    message: Mapping[str, Any],
) -> str:
    """Render one Springer-style citation from transient CrossRef metadata."""
    normalized = normalize_doi(doi)
    if not isinstance(message, Mapping):
        raise CitationFormatError(
            f"{normalized}: CrossRef citation metadata must be a mapping"
        )
    try:
        citation = _render_one(
            crossref_work_to_csl(normalized, message),
            _style_bytes(),
        )
    except Exception as error:
        raise CitationFormatError(
            f"{normalized}: CSL citation rendering failed: {error}"
        ) from error
    return citation


def format_crossref_citations(
    messages: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """Render citations for a provider batch without persisting metadata."""
    result: dict[str, str] = {}
    for raw_doi, message in messages.items():
        doi = normalize_doi(raw_doi)
        citation = format_crossref_citation(doi, message)
        if citation:
            result[doi] = citation
    return result
