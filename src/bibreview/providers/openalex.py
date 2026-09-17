"""OpenAlex discovery adapter."""

from __future__ import annotations

from ..identity import IdentityError, normalize_doi
from .http import HttpTransport


class OpenAlexError(ValueError):
    """Raised when OpenAlex returns an unexpected successful response."""


class OpenAlexProvider:
    """Discover DOI-backed works from OpenAlex using cursor pagination."""

    BASE_URL = "https://api.openalex.org/works"

    def __init__(self, transport: HttpTransport, *, api_key: str = "") -> None:
        self.transport = transport
        self.api_key = api_key.strip()

    def discover(self, query: str, *, max_pages: int = 20) -> tuple[str, ...]:
        """Return unique normalized DOI candidates in provider order."""
        query = query.strip()
        if not query:
            raise ValueError("OpenAlex discovery query must not be empty")
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1:
            raise ValueError("OpenAlex max_pages must be a positive integer")

        cursor = "*"
        seen_cursors: set[str] = set()
        seen_dois: set[str] = set()
        result: list[str] = []

        for _ in range(max_pages):
            if not cursor or cursor == "null" or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)

            params: dict[str, object] = {
                "filter": f"title_and_abstract.search:{query}",
                "per-page": 200,
                "sort": "publication_date:desc",
                "cursor": cursor,
            }
            if self.api_key:
                params["api_key"] = self.api_key

            data = self.transport.json(
                self.BASE_URL,
                params=params,
                context="OpenAlex discovery",
            )
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("results"), list)
                or not isinstance(data.get("meta"), dict)
            ):
                raise OpenAlexError("OpenAlex: unexpected page response")

            for work in data["results"]:
                if not isinstance(work, dict):
                    continue
                raw_doi = work.get("doi")
                if not isinstance(raw_doi, str) or not raw_doi.strip():
                    continue
                try:
                    doi = normalize_doi(raw_doi)
                except IdentityError:
                    continue
                if doi not in seen_dois:
                    seen_dois.add(doi)
                    result.append(doi)

            next_cursor = data["meta"].get("next_cursor")
            cursor = next_cursor if isinstance(next_cursor, str) else ""

        return tuple(result)
