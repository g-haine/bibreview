"""Semantic Scholar abstract lookup for DOI-backed publications."""

from __future__ import annotations

from time import monotonic, sleep
from typing import Callable
from urllib.parse import quote

from ..identity import normalize_doi
from .http import HttpTransport


class SemanticScholarProvider:
    """Retrieve optional publication abstracts from Semantic Scholar."""

    BASE_URL = "https://api.semanticscholar.org/graph/v1"
    BATCH_SIZE = 500
    PAPER_FIELDS = "title,abstract,year,authors,venue,externalIds"

    def __init__(
        self,
        transport: HttpTransport,
        *,
        api_key: str = "",
        min_interval_seconds: float = 0.0,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("Semantic Scholar min_interval_seconds must be non-negative")
        self.transport = transport
        self.api_key = api_key.strip()
        self.min_interval_seconds = float(min_interval_seconds)
        self._clock = clock
        self._sleeper = sleeper
        self._last_request_started: float | None = None

    def _wait_for_request_slot(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        now = self._clock()
        if self._last_request_started is not None:
            remaining = (
                self.min_interval_seconds
                - (now - self._last_request_started)
            )
            if remaining > 0:
                self._sleeper(remaining)
                now = self._clock()
        self._last_request_started = now

    def paper(self, doi: str) -> dict | None:
        """Return selected paper metadata by DOI, or None when absent."""
        normalized = normalize_doi(doi)
        headers = {"x-api-key": self.api_key} if self.api_key else None
        self._wait_for_request_slot()
        data = self.transport.json(
            f"{self.BASE_URL}/paper/DOI:{quote(normalized, safe='')}",
            params={
                "fields": self.PAPER_FIELDS
            },
            headers=headers,
            context=f"Semantic Scholar metadata for DOI {normalized}",
        )
        if data is None:
            return None
        if not isinstance(data, dict):
            return None
        return data

    def papers(self, dois: tuple[str, ...]) -> dict[str, dict]:
        """Return selected metadata for multiple DOI values in one request."""
        normalized = tuple(dict.fromkeys(normalize_doi(doi) for doi in dois))
        if not normalized:
            return {}
        if len(normalized) > self.BATCH_SIZE:
            raise ValueError(
                f"Semantic Scholar batch lookup supports at most {self.BATCH_SIZE} DOI values"
            )

        headers = {"x-api-key": self.api_key} if self.api_key else None
        self._wait_for_request_slot()
        data = self.transport.post_json(
            f"{self.BASE_URL}/paper/batch",
            params={"fields": self.PAPER_FIELDS},
            json_body={"ids": [f"DOI:{doi}" for doi in normalized]},
            headers=headers,
            context=(
                f"Semantic Scholar batch metadata for {len(normalized)} DOI values"
            ),
        )
        if not isinstance(data, list):
            raise ValueError("Semantic Scholar: unexpected batch response")

        requested = set(normalized)
        result: dict[str, dict] = {}
        for paper in data:
            if paper is None:
                continue
            if not isinstance(paper, dict):
                raise ValueError("Semantic Scholar: unexpected batch response")
            external_ids = paper.get("externalIds")
            raw_doi = (
                external_ids.get("DOI")
                if isinstance(external_ids, dict)
                else None
            )
            if not isinstance(raw_doi, str) or not raw_doi.strip():
                continue
            try:
                doi = normalize_doi(raw_doi)
            except ValueError:
                continue
            if doi in requested:
                result[doi] = paper
        return result

    def abstract(self, doi: str) -> str:
        """Return the Semantic Scholar abstract using the minimal field request."""
        normalized = normalize_doi(doi)
        headers = {"x-api-key": self.api_key} if self.api_key else None
        self._wait_for_request_slot()
        data = self.transport.json(
            f"{self.BASE_URL}/paper/DOI:{quote(normalized, safe='')}",
            params={"fields": "abstract"},
            headers=headers,
            context=f"Semantic Scholar abstract for DOI {normalized}",
        )
        if not isinstance(data, dict):
            return ""
        value = data.get("abstract")
        return value.strip() if isinstance(value, str) else ""
