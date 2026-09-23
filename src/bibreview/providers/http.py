"""Shared HTTP transport with retries and sanitized diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import monotonic, sleep
from typing import Callable
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..reporting import Reporter


class HttpError(ValueError):
    """Raised when an HTTP request fails or returns unusable content."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def _parse_retry_after(value: str | None) -> float | None:
    """Return a non-negative Retry-After delay in seconds when parseable."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None

    try:
        delay = float(normalized)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(normalized)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            (retry_at - datetime.now(timezone.utc)).total_seconds(),
        )
    return delay if delay >= 0 else None


class RateLimitedTransport:
    """Provider-local minimum-interval wrapper around an HTTP transport."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        min_interval_seconds: float = 0.0,
        rate_limit_retries: int = 2,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        if rate_limit_retries < 0:
            raise ValueError("rate_limit_retries must be non-negative")
        self.transport = transport
        self.reporter = transport.reporter
        self.min_interval_seconds = float(min_interval_seconds)
        self.rate_limit_retries = int(rate_limit_retries)
        self._clock = clock
        self._sleeper = sleeper
        self._last_request_started: float | None = None

    def _wait_for_request_slot(self, *, context: str | None = None) -> None:
        if self.min_interval_seconds <= 0:
            return

        operation = context or "HTTP request"
        now = self._clock()
        if self._last_request_started is None:
            self.reporter.debug(
                f"{operation}: rate limit request slot "
                f"(minimum interval {self.min_interval_seconds:.3f}s; first request)"
            )
            self._last_request_started = now
            return

        elapsed = now - self._last_request_started
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            self._sleeper(remaining)
            now = self._clock()
            observed_interval = now - self._last_request_started
            self.reporter.debug(
                f"{operation}: rate limit waited {remaining:.3f}s; "
                f"request slot after {observed_interval:.3f}s "
                f"(minimum interval {self.min_interval_seconds:.3f}s)"
            )
        else:
            self.reporter.debug(
                f"{operation}: rate limit request slot after {elapsed:.3f}s "
                f"(minimum interval {self.min_interval_seconds:.3f}s; no wait)"
            )
        self._last_request_started = now

    @staticmethod
    def _context(kwargs: Mapping[str, object]) -> str | None:
        context = kwargs.get("context")
        return context if isinstance(context, str) else None

    def _rate_limit_retry_delay(
        self,
        error: HttpError,
        *,
        attempt: int,
    ) -> tuple[float, str]:
        if error.retry_after_seconds is not None:
            return error.retry_after_seconds, "Retry-After"
        base = max(1.0, self.min_interval_seconds)
        return base * (2**attempt), "fallback backoff"

    def _call_with_rate_limit_retries(
        self,
        method: Callable[..., object],
        url: str,
        kwargs: Mapping[str, object],
    ) -> object:
        operation = self._context(kwargs) or "HTTP request"
        for attempt in range(self.rate_limit_retries + 1):
            self._wait_for_request_slot(context=operation)
            try:
                return method(url, **kwargs)
            except HttpError as error:
                if (
                    error.status_code != 429
                    or attempt >= self.rate_limit_retries
                ):
                    raise
                delay, source = self._rate_limit_retry_delay(
                    error,
                    attempt=attempt,
                )
                self.reporter.debug(
                    f"{operation}: HTTP 429; provider retry "
                    f"{attempt + 1}/{self.rate_limit_retries} after "
                    f"{delay:.3f}s ({source})"
                )
                if delay > 0:
                    self._sleeper(delay)
        raise AssertionError("unreachable provider retry state")

    def request(self, url: str, **kwargs: object):
        return self._call_with_rate_limit_retries(
            self.transport.request,
            url,
            kwargs,
        )

    def json(self, url: str, **kwargs: object):
        return self._call_with_rate_limit_retries(
            self.transport.json,
            url,
            kwargs,
        )

    def post_json(self, url: str, **kwargs: object):
        return self._call_with_rate_limit_retries(
            self.transport.post_json,
            url,
            kwargs,
        )

    def post_form_json(self, url: str, **kwargs: object):
        return self._call_with_rate_limit_retries(
            self.transport.post_form_json,
            url,
            kwargs,
        )


class HttpTransport:
    """Small reusable HTTP transport for BibReview providers.

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
                status_forcelist=(500, 502, 503, 504),
                allowed_methods=frozenset({"GET", "POST"}),
                raise_on_status=False,
                respect_retry_after_header=False,
            )
            self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _request_error(
        self,
        error: requests.RequestException,
        *,
        response: requests.Response | None,
        initial_host: str,
        context: str | None,
    ) -> HttpError:
        """Build one sanitized transport error without request secrets."""
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
                429: "rate limit reached",
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
        retry_after_seconds = None
        if status == 429 and failure is not None:
            headers = getattr(failure, "headers", None)
            retry_after_value = (
                headers.get("Retry-After")
                if hasattr(headers, "get")
                else None
            )
            if isinstance(retry_after_value, str):
                retry_after_seconds = _parse_retry_after(retry_after_value)

        return HttpError(
            f"{prefix}{location}: {detail}",
            status_code=status if isinstance(status, int) else None,
            retry_after_seconds=retry_after_seconds,
        )

    def request(
        self,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        context: str | None = None,
        accepted_redirect_statuses: frozenset[int] = frozenset(),
    ) -> requests.Response | None:
        """Perform one GET request and return ``None`` for HTTP 404.

        ``accepted_redirect_statuses`` is intentionally narrow: a listed status
        is accepted only after the request has redirected to another host. This
        supports DOI landing-page discovery when a publisher denies the final
        page while keeping the DOI resolver itself subject to normal failures.
        """
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
            if (
                response.status_code in accepted_redirect_statuses
                and final_host != initial_host
            ):
                return response
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            raise self._request_error(
                error,
                response=response,
                initial_host=initial_host,
                context=context,
            ) from error

    def post_form_json(
        self,
        url: str,
        *,
        data: Mapping[str, object],
        auth: tuple[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        context: str | None = None,
    ) -> object:
        """POST form data and decode JSON with the same sanitized diagnostics."""
        response = None
        initial_host = urlsplit(url).hostname or "unknown host"
        operation = context or "HTTP request"
        self.reporter.debug(f"{operation}: POST {initial_host}")
        merged_headers = {**self.default_headers, **dict(headers or {})}
        try:
            response = self.session.post(
                url,
                data=dict(data),
                auth=auth,
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
            response.raise_for_status()
        except requests.RequestException as error:
            raise self._request_error(
                error,
                response=response,
                initial_host=initial_host,
                context=context,
            ) from error

        try:
            return response.json()
        except ValueError as error:
            raise HttpError(f"{initial_host}: invalid JSON response") from error

    def post_json(
        self,
        url: str,
        *,
        json_body: Mapping[str, object],
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        context: str | None = None,
    ) -> object:
        """POST a JSON object and decode JSON with sanitized diagnostics."""
        response = None
        initial_host = urlsplit(url).hostname or "unknown host"
        operation = context or "HTTP request"
        self.reporter.debug(f"{operation}: POST {initial_host}")
        merged_headers = {**self.default_headers, **dict(headers or {})}
        try:
            response = self.session.post(
                url,
                params=params,
                json=dict(json_body),
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
            response.raise_for_status()
        except requests.RequestException as error:
            raise self._request_error(
                error,
                response=response,
                initial_host=initial_host,
                context=context,
            ) from error

        try:
            return response.json()
        except ValueError as error:
            raise HttpError(f"{initial_host}: invalid JSON response") from error

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
