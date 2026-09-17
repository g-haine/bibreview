"""CrossRef metadata adapter."""

from __future__ import annotations

from urllib.parse import quote

from ..identity import normalize_doi
from .http import HttpTransport


class CrossRefError(ValueError):
    """Raised when CrossRef returns an unexpected successful response."""


class CrossRefProvider:
    """Retrieve bibliographic metadata for a DOI from CrossRef."""

    BASE_URL = "https://api.crossref.org"

    def __init__(self, transport: HttpTransport, *, mailto: str = "") -> None:
        self.transport = transport
        self.mailto = mailto.strip()

    def work(self, doi: str) -> dict | None:
        """Return one CrossRef work message, or ``None`` when it is absent."""
        normalized = normalize_doi(doi)
        params = {"mailto": self.mailto} if self.mailto else {}
        data = self.transport.json(
            f"{self.BASE_URL}/works/{quote(normalized, safe='')}",
            params=params,
            context=f"CrossRef metadata for DOI {normalized}",
        )
        if data is None:
            return None
        if (
            not isinstance(data, dict)
            or data.get("status") != "ok"
            or not isinstance(data.get("message"), dict)
        ):
            raise CrossRefError(f"CrossRef: unexpected response for {normalized}")
        return data["message"]
