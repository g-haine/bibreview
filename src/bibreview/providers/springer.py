"""Springer Nature metadata enrichment provider."""

from __future__ import annotations

import re

from ..identity import normalize_doi
from .base import Enrichment, normalized_keywords
from .http import HttpTransport


class SpringerProvider:
    """Retrieve abstract, keywords, and conference metadata from Springer Nature."""

    def __init__(self, transport: HttpTransport, *, api_key: str) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError("Springer API key must not be empty")
        self.transport = transport
        self.api_key = key

    def enrich(self, doi: str) -> Enrichment:
        normalized = normalize_doi(doi)
        data = self.transport.json(
            "https://api.springernature.com/meta/v2/json",
            params={"q": f"doi:{normalized}", "api_key": self.api_key},
            context=f"Springer metadata for DOI {normalized}",
        )
        if not isinstance(data, dict):
            return Enrichment()
        records = data.get("records") or []
        record = records[0] if isinstance(records, list) and records and isinstance(records[0], dict) else {}
        terms = record.get("keyword") or []
        if isinstance(terms, str):
            terms = re.split(r"[,;|]", terms)
        elif not isinstance(terms, list):
            terms = []
        conferences = record.get("conferenceInfo") or []
        event = " ".join(
            str(value.get("confSeriesName", ""))
            for value in conferences
            if isinstance(value, dict) and value.get("confSeriesName")
        ).strip()
        return Enrichment(
            abstract=str(record.get("abstract") or ""),
            keywords=normalized_keywords(list(terms)),
            event=event,
        )
