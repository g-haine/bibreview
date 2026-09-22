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
    BATCH_SIZE = 25

    def __init__(self, transport: HttpTransport, *, mailto: str = "") -> None:
        self.transport = transport
        self.mailto = mailto.strip()

    def works(self, dois: tuple[str, ...]) -> dict[str, dict]:
        """Return CrossRef work messages for multiple exact DOI values."""
        normalized = tuple(dict.fromkeys(normalize_doi(doi) for doi in dois))
        if not normalized:
            return {}
        if len(normalized) > self.BATCH_SIZE:
            raise ValueError(
                f"CrossRef batch lookup supports at most {self.BATCH_SIZE} DOI values"
            )

        params: dict[str, object] = {
            "filter": ",".join(f"doi:{doi}" for doi in normalized),
            "rows": len(normalized),
        }
        if self.mailto:
            params["mailto"] = self.mailto

        data = self.transport.json(
            f"{self.BASE_URL}/works",
            params=params,
            context=f"CrossRef batch metadata for {len(normalized)} DOI values",
        )
        if (
            not isinstance(data, dict)
            or data.get("status") != "ok"
            or not isinstance(data.get("message"), dict)
            or not isinstance(data["message"].get("items"), list)
        ):
            raise CrossRefError("CrossRef: unexpected batch response")

        requested = set(normalized)
        result: dict[str, dict] = {}
        for item in data["message"]["items"]:
            if not isinstance(item, dict):
                continue
            raw_doi = item.get("DOI")
            if not isinstance(raw_doi, str) or not raw_doi.strip():
                continue
            try:
                doi = normalize_doi(raw_doi)
            except ValueError:
                continue
            if doi in requested:
                result[doi] = item
        return result

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
