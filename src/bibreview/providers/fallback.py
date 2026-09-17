"""Run-scoped abstract fallback across optional providers."""

from __future__ import annotations

from typing import Protocol

from ..reporting import Reporter
from .http import HttpError


class AbstractProvider(Protocol):
    """Minimal contract for optional abstract providers."""

    def abstract(self, doi: str) -> str:
        """Return an abstract string, or an empty string when unavailable."""


class AbstractFallback:
    """Try optional abstract providers while remembering run-scoped failures."""

    def __init__(
        self,
        *,
        semantic_scholar: AbstractProvider | None = None,
        mendeley: AbstractProvider | None = None,
        reporter: Reporter | None = None,
        unavailable_text: str = "Not available",
    ) -> None:
        self.semantic_scholar = semantic_scholar
        self.mendeley = mendeley
        self.reporter = reporter or Reporter()
        self.unavailable_text = unavailable_text
        self._semantic_scholar_limited = False
        self._mendeley_unauthorized = False

    def abstract(self, doi: str) -> str:
        """Return the longest available optional abstract for one DOI."""
        candidates: list[str] = []

        if self.semantic_scholar is not None and not self._semantic_scholar_limited:
            try:
                value = self.semantic_scholar.abstract(doi)
            except HttpError as error:
                if error.status_code != 429:
                    raise
                self._semantic_scholar_limited = True
                self.reporter.warning(
                    "Semantic Scholar HTTP 429; skipping this optional abstract provider "
                    "for the rest of this run. Other configured fallback providers will "
                    "still be tried."
                )
            else:
                if value.strip():
                    candidates.append(value.strip())

        if self.mendeley is not None and not self._mendeley_unauthorized:
            try:
                value = self.mendeley.abstract(doi)
            except HttpError as error:
                if error.status_code != 401:
                    raise
                self._mendeley_unauthorized = True
                self.reporter.warning(
                    "Mendeley HTTP 401; skipping this optional abstract provider for the "
                    "rest of this run. Check the configured Mendeley token before a future run."
                )
            else:
                if value.strip():
                    candidates.append(value.strip())

        return max(candidates, key=len, default=self.unavailable_text)
