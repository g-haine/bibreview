"""Shared contracts and normalization helpers for metadata enrichment providers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol


@dataclass(frozen=True)
class AbstractEvidence:
    """One provider abstract payload retained because it was not safely usable."""

    source: str
    value: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise TypeError("abstract evidence source must be a non-empty string")
        if not isinstance(self.value, str) or not self.value:
            raise TypeError("abstract evidence value must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise TypeError("abstract evidence reason must be a non-empty string")


@dataclass(frozen=True)
class Enrichment:
    """Provider-supplied metadata that can complement a canonical publication."""

    abstract: str = ""
    keywords: tuple[str, ...] = ()
    event: str = ""
    abstract_source: str = ""
    abstract_evidence: tuple[AbstractEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.abstract, str) or not isinstance(self.event, str):
            raise TypeError("enrichment abstract and event must be strings")
        if not isinstance(self.abstract_source, str):
            raise TypeError("enrichment abstract_source must be a string")
        keywords = tuple(self.keywords)
        if any(not isinstance(keyword, str) for keyword in keywords):
            raise TypeError("enrichment keywords must contain strings")
        evidence = tuple(self.abstract_evidence)
        if any(not isinstance(item, AbstractEvidence) for item in evidence):
            raise TypeError(
                "enrichment abstract_evidence must contain AbstractEvidence values"
            )
        object.__setattr__(self, "keywords", keywords)
        object.__setattr__(self, "abstract_evidence", evidence)


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
