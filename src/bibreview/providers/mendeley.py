"""Mendeley catalog/page abstract lookup."""

from __future__ import annotations

import html
import re
from time import monotonic
from typing import Callable

from bs4 import BeautifulSoup

from ..identity import normalize_doi
from .http import HttpError, HttpTransport


class MendeleyProvider:
    """Retrieve optional abstracts from the authenticated Mendeley catalog."""

    CATALOG_URL = "https://api.mendeley.com/catalog"
    TOKEN_URL = "https://api.mendeley.com/oauth/token"

    def __init__(
        self,
        transport: HttpTransport,
        *,
        client_id: str,
        client_secret: str,
        user_agent: str = "BibReview abstract-fallback",
        clock: Callable[[], float] = monotonic,
    ) -> None:
        normalized_id = client_id.strip()
        normalized_secret = client_secret.strip()
        if not normalized_id:
            raise ValueError("Mendeley client ID must not be empty")
        if not normalized_secret:
            raise ValueError("Mendeley client secret must not be empty")
        self.transport = transport
        self.client_id = normalized_id
        self.client_secret = normalized_secret
        self.user_agent = user_agent.strip() or "BibReview abstract-fallback"
        self._clock = clock
        self._token = ""
        self._expires_at = 0.0

    def authenticate(self) -> None:
        """Ensure that a valid client-credentials access token is cached."""
        self._access_token()

    def _access_token(self, *, force: bool = False) -> str:
        now = self._clock()
        if not force and self._token and now < self._expires_at:
            return self._token

        data = self.transport.post_form_json(
            self.TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": "all"},
            auth=(self.client_id, self.client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            context="Mendeley OAuth token exchange",
        )
        if not isinstance(data, dict):
            raise ValueError("Mendeley OAuth token response must be a JSON object")
        token = data.get("access_token")
        expires_in = data.get("expires_in")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Mendeley OAuth token response is missing access_token")
        if not isinstance(expires_in, (int, float)) or isinstance(expires_in, bool):
            raise ValueError("Mendeley OAuth token response is missing expires_in")
        lifetime = max(0.0, float(expires_in))
        self._token = token.strip()
        self._expires_at = now + max(0.0, lifetime - min(30.0, lifetime * 0.1))
        return self._token

    def _catalog(self, doi: str):
        """Fetch one catalog response, refreshing a rejected/expired token once."""
        token = self._access_token()
        try:
            return self.transport.json(
                self.CATALOG_URL,
                params={"doi": doi, "view": "all"},
                headers={
                    "Accept": "application/vnd.mendeley-document.1+json",
                    "Authorization": f"Bearer {token}",
                },
                context=f"Mendeley catalog for DOI {doi}",
            )
        except HttpError as error:
            if error.status_code != 401:
                raise
            token = self._access_token(force=True)
            return self.transport.json(
                self.CATALOG_URL,
                params={"doi": doi, "view": "all"},
                headers={
                    "Accept": "application/vnd.mendeley-document.1+json",
                    "Authorization": f"Bearer {token}",
                },
                context=f"Mendeley catalog for DOI {doi}",
            )

    def abstract(self, doi: str) -> str:
        """Return one Mendeley abstract, or an empty string when unavailable."""
        normalized = normalize_doi(doi)
        data = self._catalog(normalized)
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
