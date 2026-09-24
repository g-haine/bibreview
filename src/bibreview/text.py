"""Reusable text normalization for bibliographic metadata."""

from __future__ import annotations

import re

from unidecode import unidecode

from .structured_abstract import (
    StructuredAbstractNormalization,
    normalize_structured_abstract,
)


def slugify(value: str) -> str:
    """Return a portable ASCII slug using the established 240-character input limit."""
    if not isinstance(value, str):
        raise TypeError("slug value must be a string")
    return re.sub(r"[^a-z0-9]+", "-", unidecode(value[:240]).lower()).strip("-")


def safe_component(value: str) -> str:
    """Validate a portable single path component used for generated files."""
    if (
        not isinstance(value, str)
        or not value
        or value in {".", "..", "index"}
        or re.search(r"[/\\\x00-\x1f<>:\"|?*]", value)
    ):
        raise ValueError(f"unsafe or reserved file name: {value!r}")
    return value


_ABSTRACT_LABEL = re.compile(
    r"^(?:abstract|summary|résumé|resume|resumen|resumo|zusammenfassung|"
    r"riassunto|samenvatting)(?:\s*[:.\-–—]\s*|\s+|$)",
    re.IGNORECASE,
)


def clean_metadata(value: str, *, abstract: bool = False) -> str:
    """Remove control/JATS markup and conservatively normalize abstract labels."""
    if not isinstance(value, str):
        raise TypeError("metadata value must be a string")
    result = re.sub(r"[\t\n\r\f\v]+", " ", value)
    result = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x19]", "", result).strip()
    result = re.sub(r"<[^>]*jats[^>]*>", "", result).strip()
    if not abstract:
        return result
    result = re.sub(r"\s+", " ", result).strip()
    while result and (match := _ABSTRACT_LABEL.match(result)) is not None:
        result = result[match.end():].strip()
    return result


_ABSTRACT_MISSING_PLACEHOLDERS = frozenset({
    "not available",
})


def is_missing_metadata_value(field: str, value: str) -> bool:
    """Return whether a scalar metadata value is semantically missing.

    Empty strings are missing for every scalar field. The abstract field also
    treats the historical "Not Available" placeholder as missing, case- and
    whitespace-insensitively.
    """
    if not isinstance(field, str):
        raise TypeError("metadata field must be a string")
    if not isinstance(value, str):
        raise TypeError("metadata value must be a string")
    normalized = " ".join(value.split()).casefold()
    if not normalized:
        return True
    return field == "abstract" and normalized in _ABSTRACT_MISSING_PLACEHOLDERS


def clean_abstract(value: str) -> str:
    """Normalize one abstract and collapse known missing placeholders to empty."""
    cleaned = clean_metadata(value, abstract=True).strip()
    return "" if is_missing_metadata_value("abstract", cleaned) else cleaned


def normalize_provider_abstract(value: str) -> StructuredAbstractNormalization:
    """Prepare one provider abstract without destructive structured-markup stripping.

    Structured markup is normalized only when the dedicated structured
    normalizer can preserve its scholarly content. Refused payloads remain
    unchanged in the returned result so callers can decide how to handle the
    provider evidence without losing it.
    """
    structured = normalize_structured_abstract(value)
    if not structured.deterministic:
        return structured

    cleaned = re.sub(r"[\t\n\r\f\v]+", " ", structured.normalized)
    cleaned = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x19]",
        "",
        cleaned,
    ).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    while cleaned and (match := _ABSTRACT_LABEL.match(cleaned)) is not None:
        cleaned = cleaned[match.end():].strip()
    if is_missing_metadata_value("abstract", cleaned):
        cleaned = ""

    if cleaned == value:
        reason = "already-clean"
    elif structured.changed:
        reason = structured.reason
    else:
        reason = "text-normalized"

    return StructuredAbstractNormalization(
        normalized=cleaned,
        deterministic=True,
        changed=cleaned != value,
        reason=reason,
    )
