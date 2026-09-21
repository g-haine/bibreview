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

    def abstract(self, doi: str) -> str:
        """Return the Semantic Scholar abstract, or an empty string when absent."""
        normalized = normalize_doi(doi)
        headers = {"x-api-key": self.api_key} if self.api_key else None
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
