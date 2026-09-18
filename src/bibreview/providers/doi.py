"""DOI resolver, citation, and BibTeX content-negotiation helpers."""

from __future__ import annotations

import re
from urllib.parse import quote

from ..identity import normalize_doi
from .http import HttpTransport


class DoiProvider:
    """Retrieve DOI landing information, citations, and BibTeX text."""

    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    def landing_url(self, doi: str) -> str:
        """Return the final DOI landing URL, accepting denied redirected pages."""
        normalized = normalize_doi(doi)
        response = self.transport.request(
            f"https://doi.org/{quote(normalized, safe='')}",
            context=f"Publisher lookup for DOI {normalized}",
            accepted_redirect_statuses=frozenset({401, 403}),
        )
        return response.url if response is not None and isinstance(response.url, str) else ""

    def bibtex(self, doi: str) -> str:
        """Return BibTeX through DOI content negotiation in the established format."""
        normalized = normalize_doi(doi)
        response = self.transport.request(
            f"https://doi.org/{quote(normalized, safe='')}",
            headers={"Accept": "application/x-bibtex;q=1.0"},
            context=f"BibTeX lookup for DOI {normalized}",
        )
        return format_bibtex(response.text if response is not None else "")

    def citation(self, doi: str) -> str:
        """Return the established Springer author-date citation for a DOI."""
        normalized = normalize_doi(doi)
        response = self.transport.request(
            "https://citation.doi.org/format",
            params={
                "doi": normalized,
                "style": "springer-basic-author-date-no-et-al-with-issue",
                "lang": "en-US",
            },
            context=f"Citation lookup for DOI {normalized}",
        )
        if response is None or not response.text:
            return ""
        last = response.text.rstrip("\n").split("\n")[-1]
        value = re.sub(r"^1\.\s*", "", last)
        return value[:-1] if value else ""


def format_bibtex(value: str) -> str:
    """Normalize DOI-resolved BibTeX to BibReview's stable line-oriented format."""
    value = value.replace(" @", "@").replace("},", "},\n ")
    for field in ("series", "pages", "title"):
        value = value.replace(f", {field}", f",\n  {field}")
    lines: list[str] = []
    for line in value.split("\n"):
        if "title" in line:
            line = line.replace("={", "={{").replace("},", "}},")
        line = line.replace(" }", "\n}").replace(", }", "\n}")
        for part in line.split("\n"):
            if "month" in part or "url" in part:
                continue
            if "pages" in part:
                part = part.replace("–", "--")
            lines.append(part)
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) >= 2:
        lines[-2] = lines[-2].replace(",", "")
    result = "\n".join(lines).replace("&amp;", "\\&").rstrip("\n")
    return (
        result
        if any(line.startswith("@") for line in result.split("\n"))
        else "No BibTeX found!"
    ) + "\n"
