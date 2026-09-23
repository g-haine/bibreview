"""Reusable text normalization for bibliographic metadata."""

from __future__ import annotations

import re

from unidecode import unidecode


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
