"""Shared HTTP transport with retries and sanitized diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..reporting import Reporter


class HttpError(ValueError):
    """Raised when an HTTP request fails or returns unusable content."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class HttpTransport:
    """Small reusable GET transport for BibReview providers.

    Diagnostics intentionally expose host names and status classes only, never
    URL paths, query strings, headers, credentials, or raw exception text.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        reporter: Reporter | None = None,
        timeout: tuple[float, float] = (5, 30),
        retries: int = 3,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.reporter = reporter or Reporter()
        self.timeout = timeout
        self.default_headers = dict(default_headers or {})
        if session is None:
            retry = Retry(
                total=retries,
                backoff_factor=1,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET"}),
                raise_on_status=False,
            )
            self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def request(
        self,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        context: str | None = None,
    ) -> requests.Response | None:
        """Perform one GET request and return ``None`` for HTTP 404."""
        response = None
        initial_host = urlsplit(url).hostname or "unknown host"
        operation = context or "HTTP request"
        self.reporter.debug(f"{operation}: GET {initial_host}")
        merged_headers = {**self.default_headers, **dict(headers or {})}
        try:
            response = self.session.get(
                url,
                params=params,
                headers=merged_headers or None,
                timeout=self.timeout,
                allow_redirects=True,
            )
            final_url = response.url if isinstance(response.url, str) else url
            final_host = urlsplit(final_url).hostname or initial_host
            redirect = f" -> {final_host}" if final_host != initial_host else ""
            self.reporter.debug(
                f"{operation}: HTTP {response.status_code} from {initial_host}{redirect}"
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            failure = error.response if error.response is not None else response
            final_url = getattr(failure, "url", None)
            final_host = (
                urlsplit(final_url).hostname if isinstance(final_url, str) else None
            ) or initial_host
            location = (
                initial_host
                if final_host == initial_host
                else f"{initial_host} -> {final_host}"
            )
            status = getattr(failure, "status_code", None)
            if isinstance(status, int):
                detail = f"HTTP {status}"
                hints = {
                    401: "authentication required by the responding service",
                    403: "access denied by the responding service",
                    429: "rate limit reached after retries",
                }
                if status in hints:
                    detail += ": " + hints[status]
            elif isinstance(error, requests.exceptions.SSLError):
                detail = "TLS certificate or handshake failure"
            elif isinstance(error, requests.Timeout):
                detail = "request timed out"
            elif isinstance(error, requests.TooManyRedirects):
                detail = "too many redirects"
            elif isinstance(error, requests.ConnectionError):
                detail = "connection failed (DNS, proxy or network)"
            else:
                detail = "HTTP transport failure"
            prefix = f"{context}: " if context else ""
            raise HttpError(
                f"{prefix}{location}: {detail}",
                status_code=status if isinstance(status, int) else None,
            ) from error

    def json(self, url: str, **kwargs: object) -> object | None:
        """Return decoded JSON, preserving the transport's 404-as-absent rule."""
        response = self.request(url, **kwargs)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError as error:
            host = urlsplit(url).hostname or "unknown host"
            raise HttpError(f"{host}: invalid JSON response") from error
