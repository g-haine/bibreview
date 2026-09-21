"""Semantic Scholar abstract lookup for DOI-backed publications."""

from __future__ import annotations

from urllib.parse import quote

from ..identity import normalize_doi
from .http import HttpTransport


class SemanticScholarProvider:
    """Retrieve optional publication abstracts from Semantic Scholar."""

    BASE_URL = "https://api.semanticscholar.org/graph/v1"

    def __init__(self, transport: HttpTransport, *, api_key: str = "") -> None:
        self.transport = transport
        self.api_key = api_key.strip()

    def paper(self, doi: str) -> dict | None:
        """Return selected paper metadata by DOI, or None when absent."""
        normalized = normalize_doi(doi)
        headers = {"x-api-key": self.api_key} if self.api_key else None
        data = self.transport.json(
            f"{self.BASE_URL}/paper/DOI:{quote(normalized, safe='')}",
            params={
                "fields": "title,abstract,year,authors,venue,externalIds"
            },
            headers=headers,
            context=f"Semantic Scholar metadata for DOI {normalized}",
        )
        if data is None:
            return None
        if not isinstance(data, dict):
            return None
        return data

    def abstract(self, doi: str) -> str:
        """Return the Semantic Scholar abstract, or an empty string when absent."""
        data = self.paper(doi)
        if not isinstance(data, dict):
            return ""
        value = data.get("abstract")
        return value.strip() if isinstance(value, str) else ""
