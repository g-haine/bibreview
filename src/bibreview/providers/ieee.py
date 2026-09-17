"""IEEE Xplore metadata enrichment provider."""

from __future__ import annotations

from urllib.parse import quote

from ..identity import normalize_doi
from .base import Enrichment, normalized_keywords, plain_text
from .http import HttpTransport


class IeeeProvider:
    """Retrieve abstract, keywords, and conference metadata from IEEE Xplore."""

    def __init__(self, transport: HttpTransport, *, api_key: str) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError("IEEE API key must not be empty")
        self.transport = transport
        self.api_key = key

    def enrich(self, doi: str) -> Enrichment:
        normalized = normalize_doi(doi)
        data = self.transport.json(
            f"https://ieeexploreapi.ieee.org/api/v1/articles/doi/{quote(normalized, safe='')}",
            params={"apikey": self.api_key, "format": "json"},
            context=f"IEEE metadata for DOI {normalized}",
        )
        if not isinstance(data, dict):
            return Enrichment()
        articles = data.get("articles") or []
        record = articles[0] if isinstance(articles, list) and articles and isinstance(articles[0], dict) else {}
        nested = record.get("index_terms") or {}
        if not isinstance(nested, dict):
            nested = {}
        terms: list[object] = []
        for source in (record, nested):
            for name in ("author_terms", "ieee_terms"):
                values = source.get(name)
                if isinstance(values, list):
                    terms.extend(values)
        event = (
            str(record.get("publication_title") or "")
            if str(record.get("content_type") or "").lower() == "conferences"
            else ""
        )
        return Enrichment(
            abstract=plain_text(record.get("abstract")),
            keywords=normalized_keywords(terms),
            event=event,
        )
