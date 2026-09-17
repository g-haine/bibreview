"""Reusable text normalization extracted from the PHRAISE maintenance code."""

from __future__ import annotations

import re

from unidecode import unidecode


def slugify(value: str) -> str:
    """Return the established portable ASCII slug with the legacy 240-char limit."""
    if not isinstance(value, str):
        raise TypeError("slug value must be a string")
    return re.sub(r"[^a-z0-9]+", "-", unidecode(value[:240]).lower()).strip("-")


def clean_metadata(value: str, *, abstract: bool = False) -> str:
    """Remove control/JATS markup while preserving the current PHRAISE contract."""
    if not isinstance(value, str):
        raise TypeError("metadata value must be a string")
    result = re.sub(r"[\x00-\x19]", "", value).strip()
    result = re.sub(r"<[^>]*jats[^>]*>", "", result)
    return re.sub(r"summary|abstract", "", result, flags=re.I) if abstract else result
