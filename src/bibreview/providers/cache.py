"""Persistent raw provider-response caching for deterministic network requests."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from ..reporting import Reporter
from .http import HttpError


_CACHE_SCHEMA_VERSION = 1
_MISS = object()
_VALID_MODES = frozenset({"normal", "no-cache", "refresh"})


def default_provider_cache_directory() -> Path:
    """Return the user-level provider cache directory without touching the project."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base).expanduser() if base else Path.home() / "AppData" / "Local"
        return root / "BibReview" / "Cache" / "providers-v1"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "BibReview" / "providers-v1"
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "bibreview" / "providers-v1"


def _canonical(value: object) -> object:
    """Return a deterministic JSON-safe representation used only for hashing."""
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_canonical(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _request_key(operation: str, url: str, kwargs: Mapping[str, object]) -> str:
    """Hash one complete deterministic request without persisting request secrets."""
    identity = {
        "operation": operation,
        "url": url,
        "arguments": {
            key: _canonical(value)
            for key, value in sorted(kwargs.items())
            if key != "context"
        },
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ProviderResponseCache:
    """Small file cache whose entry mtime is the immutable freshness timestamp."""

    def __init__(
        self,
        *,
        ttl_hours: float,
        root: Path | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if ttl_hours <= 0:
            raise ValueError("cache ttl_hours must be greater than zero")
        self.ttl_seconds = float(ttl_hours) * 3600.0
        self.root = root or default_provider_cache_directory()
        if clock is None:
            import time

            self._clock = time.time
        else:
            self._clock = clock

    def entry_path(self, key: str) -> Path:
        """Return the opaque path for one hashed request key."""
        return self.root / key[:2] / f"{key}.json"

    def read(self, key: str) -> object:
        """Return a fresh cache payload or a private miss sentinel."""
        path = self.entry_path(key)
        try:
            stat = path.stat()
        except OSError:
            return _MISS
        age = max(0.0, self._clock() - stat.st_mtime)
        if age >= self.ttl_seconds:
            return _MISS
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return _MISS
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != _CACHE_SCHEMA_VERSION
            or "payload" not in data
        ):
            return _MISS
        return data["payload"]

    def write(self, key: str, payload: object) -> None:
        """Atomically replace one cache entry after a successful provider response."""
        path = self.entry_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "payload": payload,
        }
        serialized = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
            ) as stream:
                stream.write(serialized)
                stream.write("\n")
                temporary = Path(stream.name)
            os.replace(temporary, path)
            now = self._clock()
            os.utime(path, (now, now))
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass


class CachedTransport:
    """Transparent cache wrapper around a provider HTTP transport.

    GET responses are stored before provider-specific normalization. JSON POST
    responses are stored as the decoded provider payload because the underlying
    transport exposes no raw POST response object. OAuth/form POSTs are never
    cached.
    """

    def __init__(
        self,
        transport: object,
        *,
        cache: ProviderResponseCache | None,
        mode: str = "normal",
        reporter: Reporter | None = None,
    ) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f"unsupported cache mode: {mode}")
        self.transport = transport
        self.cache = cache
        self.mode = mode
        self.reporter = reporter or getattr(transport, "reporter", Reporter())

    @property
    def _can_read(self) -> bool:
        return self.cache is not None and self.mode == "normal"

    @property
    def _can_write(self) -> bool:
        return self.cache is not None and self.mode in {"normal", "refresh"}

    def _label(self, url: str, kwargs: Mapping[str, object]) -> str:
        context = kwargs.get("context")
        if isinstance(context, str) and context:
            return context
        return urlsplit(url).hostname or "provider request"

    def _read(self, key: str, *, url: str, kwargs: Mapping[str, object]) -> object:
        if not self._can_read:
            return _MISS
        try:
            payload = self.cache.read(key) if self.cache is not None else _MISS
        except OSError:
            return _MISS
        if payload is not _MISS:
            self.reporter.debug(f"{self._label(url, kwargs)}: provider cache hit")
        return payload

    def _write(
        self,
        key: str,
        payload: object,
        *,
        url: str,
        kwargs: Mapping[str, object],
    ) -> None:
        if not self._can_write:
            return
        try:
            assert self.cache is not None
            self.cache.write(key, payload)
        except (OSError, TypeError, ValueError):
            self.reporter.debug(
                f"{self._label(url, kwargs)}: provider cache write skipped"
            )

    @staticmethod
    def _response_payload(response: requests.Response) -> dict[str, object]:
        return {
            "kind": "response",
            "status_code": int(response.status_code),
            "url": response.url if isinstance(response.url, str) else "",
            "encoding": response.encoding or "",
            "content_b64": base64.b64encode(response.content).decode("ascii"),
        }

    @staticmethod
    def _restore_response(payload: Mapping[str, object]) -> requests.Response:
        response = requests.Response()
        response.status_code = int(payload.get("status_code", 200))
        response.url = str(payload.get("url", ""))
        encoding = payload.get("encoding")
        response.encoding = str(encoding) if encoding else None
        content = payload.get("content_b64")
        if not isinstance(content, str):
            raise ValueError("cached response is missing content")
        response._content = base64.b64decode(content.encode("ascii"))
        return response

    @staticmethod
    def _cacheable_response(response: requests.Response) -> bool:
        status = response.status_code
        return status not in {401, 403, 429} and not (500 <= status <= 599)

    def request(self, url: str, **kwargs: object) -> requests.Response | None:
        key = _request_key("GET", url, kwargs)
        payload = self._read(key, url=url, kwargs=kwargs)
        if isinstance(payload, Mapping):
            kind = payload.get("kind")
            if kind == "missing":
                return None
            if kind == "response":
                try:
                    return self._restore_response(payload)
                except (TypeError, ValueError):
                    pass

        response = self.transport.request(url, **kwargs)
        if response is None:
            self._write(
                key,
                {"kind": "missing"},
                url=url,
                kwargs=kwargs,
            )
        elif isinstance(response, requests.Response) and self._cacheable_response(response):
            self._write(
                key,
                self._response_payload(response),
                url=url,
                kwargs=kwargs,
            )
        return response

    def json(self, url: str, **kwargs: object) -> object | None:
        response = self.request(url, **kwargs)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError as error:
            host = urlsplit(url).hostname or "unknown host"
            raise HttpError(f"{host}: invalid JSON response") from error

    def post_json(self, url: str, **kwargs: object) -> object:
        key = _request_key("POST_JSON", url, kwargs)
        payload = self._read(key, url=url, kwargs=kwargs)
        if isinstance(payload, Mapping) and payload.get("kind") == "json":
            return payload.get("value")

        value = self.transport.post_json(url, **kwargs)
        self._write(
            key,
            {"kind": "json", "value": value},
            url=url,
            kwargs=kwargs,
        )
        return value

    def post_form_json(self, url: str, **kwargs: object) -> object:
        """Always perform form/OAuth requests live and never cache them."""
        return self.transport.post_form_json(url, **kwargs)
