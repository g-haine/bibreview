"""Run-scoped abstract fallback across optional providers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..reporting import Reporter
from ..text import clean_abstract
from .http import HttpError


class AbstractProvider(Protocol):
    """Minimal contract for optional abstract providers."""

    def abstract(self, doi: str) -> str:
        """Return an abstract string, or an empty string when unavailable."""


class AbstractFallback:
    """Try optional abstract providers while remembering run-scoped failures."""

    def __init__(
        self,
        *,
        semantic_scholar: AbstractProvider | None = None,
        mendeley: AbstractProvider | None = None,
        openalex: AbstractProvider | None = None,
        reporter: Reporter | None = None,
        unavailable_text: str = "",
    ) -> None:
        self.semantic_scholar = semantic_scholar
        self.mendeley = mendeley
        self.openalex = openalex
        self.reporter = reporter or Reporter()
        self.unavailable_text = unavailable_text
        self._semantic_scholar_limited = False
        self._mendeley_unauthorized = False
        self._openalex_limited = False

    @staticmethod
    def _batch_capability(provider: AbstractProvider) -> tuple[int, object | None]:
        abstracts = getattr(provider, "abstracts", None)
        size = getattr(provider, "BATCH_SIZE", 1)
        if not callable(abstracts):
            return 1, None
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ValueError("abstract provider BATCH_SIZE must be a positive integer")
        return size, abstracts

    def _warn_limited(self, provider_name: str) -> None:
        self.reporter.warning(
            f"{provider_name} HTTP 429; skipping this optional abstract provider "
            "for the rest of this run. Other configured fallback providers will "
            "still be tried."
        )

    def _individual_values(
        self,
        provider: AbstractProvider,
        dois: tuple[str, ...],
        *,
        provider_name: str,
        limited_attr: str,
    ) -> dict[str, str]:
        values: dict[str, str] = {}
        for doi in dois:
            if getattr(self, limited_attr):
                break
            try:
                value = provider.abstract(doi)
            except HttpError as error:
                if error.status_code == 429:
                    setattr(self, limited_attr, True)
                    self._warn_limited(provider_name)
                    break
                self.reporter.warning(
                    f"{provider_name} abstract lookup failed for {doi}; "
                    "continuing with other fallback providers."
                )
                continue
            except (OSError, ValueError, TypeError):
                self.reporter.warning(
                    f"{provider_name} abstract lookup failed for {doi}; "
                    "continuing with other fallback providers."
                )
                continue
            values[doi] = clean_abstract(value)
        return values

    def _provider_values_many(
        self,
        provider: AbstractProvider | None,
        dois: tuple[str, ...],
        *,
        provider_name: str,
        limited_attr: str,
    ) -> dict[str, str]:
        if provider is None or getattr(self, limited_attr) or not dois:
            return {}

        size, abstracts = self._batch_capability(provider)
        if abstracts is None or size <= 1:
            return self._individual_values(
                provider,
                dois,
                provider_name=provider_name,
                limited_attr=limited_attr,
            )

        result: dict[str, str] = {}
        for offset in range(0, len(dois), size):
            if getattr(self, limited_attr):
                break
            chunk = dois[offset : offset + size]
            try:
                batch = abstracts(chunk)
                if not isinstance(batch, Mapping):
                    raise TypeError("batch abstract lookup must return a mapping")
                for doi in chunk:
                    value = batch.get(doi, "")
                    if not isinstance(value, str):
                        raise TypeError(
                            f"batch abstract lookup returned a non-string value for {doi}"
                        )
                    result[doi] = clean_abstract(value)
            except HttpError as error:
                if error.status_code == 429:
                    setattr(self, limited_attr, True)
                    self._warn_limited(provider_name)
                    break
                self.reporter.warning(
                    f"{provider_name}: batch of {len(chunk)} DOI values failed; "
                    "falling back to individual abstract lookups for that chunk."
                )
                result.update(
                    self._individual_values(
                        provider,
                        chunk,
                        provider_name=provider_name,
                        limited_attr=limited_attr,
                    )
                )
            except (OSError, ValueError, TypeError):
                self.reporter.warning(
                    f"{provider_name}: batch of {len(chunk)} DOI values failed; "
                    "falling back to individual abstract lookups for that chunk."
                )
                result.update(
                    self._individual_values(
                        provider,
                        chunk,
                        provider_name=provider_name,
                        limited_attr=limited_attr,
                    )
                )
        return result

    def abstract_many(self, dois: tuple[str, ...]) -> dict[str, str]:
        """Return the longest optional abstract for each DOI in input order."""
        normalized = tuple(dict.fromkeys(dois))
        candidates: dict[str, list[str]] = {doi: [] for doi in normalized}

        semantic = self._provider_values_many(
            self.semantic_scholar,
            normalized,
            provider_name="Semantic Scholar",
            limited_attr="_semantic_scholar_limited",
        )
        openalex = self._provider_values_many(
            self.openalex,
            normalized,
            provider_name="OpenAlex",
            limited_attr="_openalex_limited",
        )

        for values in (semantic, openalex):
            for doi in normalized:
                value = clean_abstract(values.get(doi, ""))
                if value:
                    candidates[doi].append(value)

        if self.mendeley is not None and not self._mendeley_unauthorized:
            for doi in normalized:
                if self._mendeley_unauthorized:
                    break
                try:
                    value = self.mendeley.abstract(doi)
                except HttpError as error:
                    if error.status_code == 401:
                        self._mendeley_unauthorized = True
                        self.reporter.warning(
                            "Mendeley HTTP 401; skipping this optional abstract provider "
                            "for the rest of this run. Check the configured Mendeley token "
                            "before a future run."
                        )
                        break
                    self.reporter.warning(
                        f"Mendeley abstract lookup failed for {doi}; "
                        "continuing with other fallback providers."
                    )
                    continue
                except (OSError, ValueError, TypeError):
                    self.reporter.warning(
                        f"Mendeley abstract lookup failed for {doi}; "
                        "continuing with other fallback providers."
                    )
                    continue
                cleaned = clean_abstract(value)
                if cleaned:
                    candidates[doi].append(cleaned)

        default = clean_abstract(self.unavailable_text)
        return {
            doi: max(candidates[doi], key=len, default=default)
            for doi in normalized
        }

    def abstract(self, doi: str) -> str:
        """Return the longest available optional abstract for one DOI."""
        candidates: list[str] = []

        if self.semantic_scholar is not None and not self._semantic_scholar_limited:
            try:
                value = self.semantic_scholar.abstract(doi)
            except HttpError as error:
                if error.status_code != 429:
                    raise
                self._semantic_scholar_limited = True
                self._warn_limited("Semantic Scholar")
            else:
                cleaned = clean_abstract(value)
                if cleaned:
                    candidates.append(cleaned)

        if self.openalex is not None and not self._openalex_limited:
            try:
                value = self.openalex.abstract(doi)
            except HttpError as error:
                if error.status_code != 429:
                    raise
                self._openalex_limited = True
                self._warn_limited("OpenAlex")
            else:
                cleaned = clean_abstract(value)
                if cleaned:
                    candidates.append(cleaned)

        if self.mendeley is not None and not self._mendeley_unauthorized:
            try:
                value = self.mendeley.abstract(doi)
            except HttpError as error:
                if error.status_code != 401:
                    raise
                self._mendeley_unauthorized = True
                self.reporter.warning(
                    "Mendeley HTTP 401; skipping this optional abstract provider for the "
                    "rest of this run. Check the configured Mendeley token before a future run."
                )
            else:
                cleaned = clean_abstract(value)
                if cleaned:
                    candidates.append(cleaned)

        candidates = [value for value in candidates if value]
        return max(
            candidates,
            key=len,
            default=clean_abstract(self.unavailable_text),
        )
