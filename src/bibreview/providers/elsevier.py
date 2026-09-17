"""Elsevier/Scopus metadata enrichment provider."""

from __future__ import annotations

import re
from urllib.parse import quote

from ..identity import normalize_doi
from .base import Enrichment, normalized_keywords, plain_text
from .http import HttpTransport


class ElsevierProvider:
    """Retrieve abstract, keywords, and issue/event metadata from Elsevier."""

    def __init__(self, transport: HttpTransport, *, api_key: str) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError("Elsevier API key must not be empty")
        self.transport = transport
        self.api_key = key

    def enrich(self, doi: str) -> Enrichment:
        normalized = normalize_doi(doi)
        data = self.transport.json(
            f"https://api.elsevier.com/content/article/doi/{quote(normalized, safe='')}",
            headers={"Accept": "application/json", "X-ELS-APIKey": self.api_key},
            context=f"Scopus metadata for DOI {normalized}",
        )
        if not isinstance(data, dict):
            return Enrichment()
        core = data.get("full-text-retrieval-response", {}).get("coredata", {})
        if not isinstance(core, dict):
            return Enrichment()
        subjects = [
            value.get("$", "")
            for value in (core.get("dcterms:subject") or [])
            if isinstance(value, dict)
        ]
        terms = re.split(r"[|;,]", str(core.get("authkeywords") or ""))
        return Enrichment(
            abstract=plain_text(core.get("dc:description")),
            keywords=normalized_keywords([*subjects, *terms]),
            event=plain_text(core.get("prism:issueName")),
        )
