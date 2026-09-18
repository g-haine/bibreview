"""arXiv Atom provider with conservative retry behavior."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .model import ArxivEntry


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
RETRY_DELAYS_SECONDS = (60, 180, 600)
MAX_RETRY_AFTER_SECONDS = 300
RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class ArxivError(RuntimeError):
    """Raised when arXiv data cannot be represented safely."""


class TemporaryArxivError(ArxivError):
    """Raised after a transient arXiv failure exhausts the retry policy."""


def _clean(text: str | None) -> str:
    return (text or "").strip()


class ArxivProvider:
    """Fetch display-oriented entries from the arXiv Atom API."""

    def __init__(
        self,
        *,
        query: str,
        max_results: int,
        sort_by: str,
        sort_order: str,
        user_agent: str,
        contact_email: str,
        opener: Callable[..., object] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.query = query
        self.max_results = max_results
        self.sort_by = sort_by
        self.sort_order = sort_order
        self.user_agent = user_agent
        self.contact_email = contact_email
        self._opener = opener
        self._sleeper = sleeper
        self._now = now or (lambda: datetime.now(timezone.utc))

    def request(self) -> Request:
        """Build the configured arXiv request."""
        params = urlencode(
            {
                "search_query": self.query,
                "start": 0,
                "max_results": self.max_results,
                "sortBy": self.sort_by,
                "sortOrder": self.sort_order,
            }
        )
        return Request(
            f"{ARXIV_API}?{params}",
            headers={
                "User-Agent": self.user_agent,
                "From": self.contact_email,
                "Accept": "application/atom+xml",
            },
        )

    def _retry_after_seconds(self, error: HTTPError) -> int | None:
        value = error.headers.get("Retry-After") if error.headers else None
        if not value:
            return None
        try:
            seconds = math.ceil(float(value))
        except (ValueError, OverflowError):
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = math.ceil(
                    (retry_at - self._now()).total_seconds()
                )
            except (TypeError, ValueError, OverflowError):
                return None
        return min(max(seconds, 1), MAX_RETRY_AFTER_SECONDS)

    def _fetch_xml(self) -> bytes:
        request = self.request()
        attempts = len(RETRY_DELAYS_SECONDS) + 1

        for attempt in range(attempts):
            retry_after: int | None = None
            try:
                response = self._opener(request, timeout=30)
                with response:
                    return response.read()
            except HTTPError as error:
                if error.code not in RETRYABLE_HTTP_STATUS_CODES:
                    raise ArxivError(
                        f"arXiv returned HTTP {error.code}"
                    ) from error
                failure = f"arXiv returned HTTP {error.code}"
                retry_after = self._retry_after_seconds(error)
                if error.code == 429 and retry_after is None:
                    raise TemporaryArxivError(
                        f"{failure} without Retry-After; not retrying"
                    ) from None
            except (URLError, TimeoutError) as error:
                failure = f"arXiv request failed: {error}"

            if attempt == attempts - 1:
                raise TemporaryArxivError(
                    f"{failure} after {attempts} attempts"
                ) from None

            delay = retry_after or RETRY_DELAYS_SECONDS[attempt]
            self._sleeper(delay)

        raise AssertionError("unreachable")

    def fetch(self) -> tuple[ArxivEntry, ...]:
        """Fetch and parse the configured arXiv feed."""
        try:
            root = ET.fromstring(self._fetch_xml())
        except ET.ParseError as error:
            raise ArxivError(f"invalid arXiv Atom response: {error}") from error

        entries: list[ArxivEntry] = []
        for item in root.findall("atom:entry", ATOM_NS):
            updated_text = _clean(
                item.findtext("atom:updated", namespaces=ATOM_NS)
            ).split("T", 1)[0]
            try:
                updated = date.fromisoformat(updated_text)
            except ValueError as error:
                raise ArxivError(
                    f"invalid arXiv updated date: {updated_text!r}"
                ) from error

            entries.append(
                ArxivEntry(
                    title=_clean(
                        item.findtext("atom:title", namespaces=ATOM_NS)
                    ),
                    summary=_clean(
                        item.findtext("atom:summary", namespaces=ATOM_NS)
                    ),
                    url=_clean(
                        item.findtext("atom:id", namespaces=ATOM_NS)
                    ),
                    authors=tuple(
                        _clean(
                            author.findtext(
                                "atom:name",
                                namespaces=ATOM_NS,
                            )
                        )
                        for author in item.findall("atom:author", ATOM_NS)
                    ),
                    updated=updated,
                )
            )

        if not entries:
            raise ArxivError(
                "arXiv returned no entries; refusing to overwrite the cache"
            )
        return tuple(entries)
