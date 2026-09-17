"""Mendeley catalog/page abstract lookup."""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup

from ..identity import normalize_doi
from .http import HttpTransport


class MendeleyProvider:
    """Retrieve optional abstracts from the authenticated Mendeley catalog."""

    CATALOG_URL = "https://api.mendeley.com/catalog"

    def __init__(
        self,
        transport: HttpTransport,
        *,
        token: str,
        user_agent: str = "BibReview abstract-fallback",
    ) -> None:
        normalized_token = token.strip()
        if not normalized_token:
            raise ValueError("Mendeley token must not be empty")
        self.transport = transport
        self.token = normalized_token
        self.user_agent = user_agent.strip() or "BibReview abstract-fallback"

    def abstract(self, doi: str) -> str:
        """Return one Mendeley abstract, or an empty string when unavailable."""
        normalized = normalize_doi(doi)
        data = self.transport.json(
            self.CATALOG_URL,
            params={"doi": normalized, "view": "all"},
            headers={
                "Accept": "application/vnd.mendeley-document.1+json",
                "Authorization": f"Bearer {self.token}",
            },
            context=f"Mendeley catalog for DOI {normalized}",
        )
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            return ""
        link = data[0].get("link")
        if not isinstance(link, str) or not link.strip():
            return ""
        response = self.transport.request(
            link,
            headers={"User-Agent": self.user_agent, "Accept": "text/html"},
            context=f"Mendeley abstract page for DOI {normalized}",
        )
        return mendeley_abstract(response.text) if response is not None else ""


def mendeley_abstract(markup: str) -> str:
    """Extract a substantial abstract from known Mendeley/publisher HTML shapes."""
    if not isinstance(markup, str):
        raise TypeError("Mendeley markup must be a string")

    soup = BeautifulSoup(markup, "html.parser")

    def clean(value: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(value or "")).strip()

    result = ""
    title = soup.find(
        lambda tag: tag.name in ("h2", "h3", "h4", "h5")
        and (
            tag.get("data-name") == "abstract-title"
            or re.fullmatch(r"\s*abstract\s*", tag.get_text(), re.I)
        )
    )
    if title:
        container = title.find_parent(["div", "section", "article"]) or title.parent
        card = container
        while card and not any(
            css_class == "card" or css_class.startswith("card__")
            for css_class in card.get("class", [])
        ):
            card = card.parent
        target = (card or container).select_one('p[data-name="content"]')
        if target:
            spans = target.find_all("span")
            result = clean(
                " ".join(span.get_text(strip=True) for span in spans)
                if spans
                else target.get_text(strip=True)
            )

    if not result:
        for selector in (
            "#Abs1-content",
            "section.Abstract",
            "div.Abstract",
            "div#abstract",
            "section#abstract",
            "div.article__abstract",
            "article .abstract",
        ):
            element = soup.select_one(selector)
            if element:
                value = clean(element.get_text(" ", strip=True))
                if len(value.split()) > 30:
                    result = value
                    break

    if not result:
        for key in (
            "citation_abstract",
            "dcterms.abstract",
            "DC.Description",
            "dc.Description",
            "og:description",
            "description",
        ):
            element = soup.find("meta", attrs={"name": key}) or soup.find(
                "meta", attrs={"property": key}
            )
            value = clean(element.get("content", "")) if element else ""
            if len(value.split()) >= 40 and not value.endswith("..."):
                result = value
                break

    result = re.sub(
        r"^(abstract|résumé|summary|zusammenfassung)\s*[:–—-]\s*",
        "",
        result,
        flags=re.I,
    )
    return result if len(result.split()) >= 40 else ""
