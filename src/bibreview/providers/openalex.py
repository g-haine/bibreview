"""OpenAlex discovery adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from urllib.parse import quote

from ..identity import IdentityError, normalize_doi
from .http import HttpTransport


_OPENALEX_ABSTRACT_PLACEHOLDERS = {
    "accepted version",
    "international audience",
}
_OPENALEX_VIDEO_PREFIX = re.compile(
    r"^View Video Presentation:\s*https?://\S+\s*",
    re.IGNORECASE,
)


def openalex_abstract(value: object) -> str:
    """Reconstruct and sanitize an OpenAlex inverted-index abstract."""
    if not isinstance(value, Mapping) or not value:
        return ""
    positions: list[tuple[int, str]] = []
    for word, raw_positions in value.items():
        if not isinstance(word, str) or not isinstance(raw_positions, list):
            continue
        for raw_position in raw_positions:
            if isinstance(raw_position, int) and not isinstance(raw_position, bool):
                positions.append((raw_position, word))
    positions.sort(key=lambda item: item[0])
    abstract = " ".join(word for _, word in positions).strip()
    abstract = _OPENALEX_VIDEO_PREFIX.sub("", abstract).strip()
    if abstract.casefold() in _OPENALEX_ABSTRACT_PLACEHOLDERS:
        return ""
    return abstract


class OpenAlexError(ValueError):
    """Raised when OpenAlex returns an unexpected successful response."""



@dataclass(frozen=True)
class OpenAlexDiscoveryResult:
    """Detailed outcome of one OpenAlex discovery query."""

    candidates: tuple[str, ...]
    total_matches: int | None
    pages_fetched: int
    works_examined: int
    truncated: bool

    def data(self) -> dict[str, object]:
        """Return a JSON-serializable diagnostic representation."""
        return {
            "total_matches": self.total_matches,
            "pages_fetched": self.pages_fetched,
            "works_examined": self.works_examined,
            "doi_candidates": len(self.candidates),
            "truncated": self.truncated,
        }


class OpenAlexProvider:
    """Discover DOI-backed works from OpenAlex using cursor pagination."""

    BASE_URL = "https://api.openalex.org/works"
    BATCH_SIZE = 100
    SELECT_FIELDS = (
        "id,doi,title,type,publication_year,authorships,"
        "primary_location,biblio,abstract_inverted_index"
    )

    def __init__(self, transport: HttpTransport, *, api_key: str = "") -> None:
        self.transport = transport
        self.api_key = api_key.strip()

    def work(self, doi: str) -> dict | None:
        """Return one OpenAlex work by DOI, or None when it is absent."""
        normalized = normalize_doi(doi)
        params: dict[str, object] = {"select": self.SELECT_FIELDS}
        if self.api_key:
            params["api_key"] = self.api_key
        data = self.transport.json(
            f"{self.BASE_URL}/https://doi.org/{quote(normalized, safe='/')}",
            params=params,
            context=f"OpenAlex metadata for DOI {normalized}",
        )
        if data is None:
            return None
        if not isinstance(data, dict):
            raise OpenAlexError("OpenAlex: unexpected work response")
        return data

    def abstract(self, doi: str) -> str:
        """Return one reconstructed OpenAlex abstract, or an empty string."""
        work = self.work(doi)
        if work is None:
            return ""
        return openalex_abstract(work.get("abstract_inverted_index"))

    def abstracts(self, dois: tuple[str, ...]) -> dict[str, str]:
        """Return reconstructed abstracts for multiple DOI values."""
        normalized = tuple(dict.fromkeys(normalize_doi(doi) for doi in dois))
        records = self.works(normalized)
        return {
            doi: openalex_abstract(records[doi].get("abstract_inverted_index"))
            for doi in normalized
            if doi in records
        }

    def works(self, dois: tuple[str, ...]) -> dict[str, dict]:
        """Return OpenAlex works for multiple DOI values in one request."""
        normalized = tuple(dict.fromkeys(normalize_doi(doi) for doi in dois))
        if not normalized:
            return {}
        if len(normalized) > self.BATCH_SIZE:
            raise ValueError(
                f"OpenAlex batch lookup supports at most {self.BATCH_SIZE} DOI values"
            )

        params: dict[str, object] = {
            "filter": "doi:" + "|".join(
                f"https://doi.org/{doi}" for doi in normalized
            ),
            "per-page": len(normalized),
            "select": self.SELECT_FIELDS,
        }
        if self.api_key:
            params["api_key"] = self.api_key
        data = self.transport.json(
            self.BASE_URL,
            params=params,
            context=f"OpenAlex batch metadata for {len(normalized)} DOI values",
        )
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise OpenAlexError("OpenAlex: unexpected batch response")

        requested = set(normalized)
        result: dict[str, dict] = {}
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
            if doi in requested:
                result[doi] = work
        return result

    def discover_detailed(
        self,
        query: str,
        *,
        max_pages: int = 20,
    ) -> OpenAlexDiscoveryResult:
        """Return DOI candidates plus paging/truncation diagnostics."""
        query = query.strip()
        if not query:
            raise ValueError("OpenAlex discovery query must not be empty")
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1:
            raise ValueError("OpenAlex max_pages must be a positive integer")

        cursor = "*"
        seen_cursors: set[str] = set()
        seen_dois: set[str] = set()
        result: list[str] = []
        total_matches: int | None = None
        pages_fetched = 0
        works_examined = 0
        exhausted = False

        for _ in range(max_pages):
            if not cursor or cursor == "null":
                exhausted = True
                break
            if cursor in seen_cursors:
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

            meta = data["meta"]
            raw_count = meta.get("count")
            if raw_count is not None:
                if (
                    not isinstance(raw_count, int)
                    or isinstance(raw_count, bool)
                    or raw_count < 0
                ):
                    raise OpenAlexError("OpenAlex: unexpected meta.count")
                if total_matches is None:
                    total_matches = raw_count

            pages_fetched += 1
            works_examined += len(data["results"])

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

            next_cursor = meta.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == "null":
                cursor = ""
                exhausted = True
            else:
                cursor = next_cursor

        return OpenAlexDiscoveryResult(
            candidates=tuple(result),
            total_matches=total_matches,
            pages_fetched=pages_fetched,
            works_examined=works_examined,
            truncated=not exhausted,
        )

    def discover(self, query: str, *, max_pages: int = 20) -> tuple[str, ...]:
        """Return unique normalized DOI candidates in provider order."""
        return self.discover_detailed(query, max_pages=max_pages).candidates
