"""Shared contracts and normalization helpers for metadata enrichment providers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol


@dataclass(frozen=True)
class Enrichment:
    """Provider-supplied metadata that can complement a canonical publication."""

    abstract: str = ""
    keywords: tuple[str, ...] = ()
    event: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.abstract, str) or not isinstance(self.event, str):
            raise TypeError("enrichment abstract and event must be strings")
        normalized = tuple(self.keywords)
        if any(not isinstance(keyword, str) for keyword in normalized):
            raise TypeError("enrichment keywords must contain strings")
        object.__setattr__(self, "keywords", normalized)


class EnrichmentProvider(Protocol):
    """Minimal contract implemented by publisher metadata providers."""

    def enrich(self, doi: str) -> Enrichment:
        """Return enrichment metadata for one DOI, or an empty result."""


def plain_text(value: object) -> str:
    """Strip simple markup and normalize whitespace from provider text fields."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(value or ""))).strip()


def normalized_keywords(values: list[object]) -> tuple[str, ...]:
    """Return sorted, lower-case, unique non-empty keywords."""
    return tuple(
        sorted({str(value).lower().strip() for value in values if str(value or "").strip()})
    )
