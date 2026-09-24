"""Run-scoped abstract fallback across optional providers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ..reporting import Reporter
from ..text import normalize_provider_abstract
from .base import AbstractEvidence
from .http import HttpError


class AbstractProvider(Protocol):
    """Minimal contract for optional abstract providers."""

    def abstract(self, doi: str) -> str:
        """Return an abstract string, or an empty string when unavailable."""


@dataclass(frozen=True)
class AbstractFallbackSelection:
    """Selected safe fallback abstract plus any refused provider evidence."""

    abstract: str = ""
    source: str = ""
    evidence: tuple[AbstractEvidence, ...] = ()


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

    @staticmethod
    def _source(provider_name: str) -> str:
        return provider_name.casefold().replace(" ", "_")

    def _warn_limited(self, provider_name: str) -> None:
        self.reporter.warning(
            f"{provider_name} HTTP 429; skipping this optional abstract provider "
            "for the rest of this run. Other configured fallback providers will "
            "still be tried."
        )

    def _clean_candidate(
        self,
        value: str,
        *,
        doi: str,
        provider_name: str,
    ) -> tuple[str, AbstractEvidence | None]:
        """Return one safe abstract and retain refused structured markup as evidence."""
        result = normalize_provider_abstract(value)
        if result.deterministic:
            return result.normalized, None
        self.reporter.warning(
            f"{provider_name} abstract for {doi} contains unsupported structured "
            f"markup ({result.reason}); continuing with other fallback providers."
        )
        return "", AbstractEvidence(
            source=self._source(provider_name),
            value=result.normalized,
            reason=result.reason,
        )

    def _individual_values(
        self,
        provider: AbstractProvider,
        dois: tuple[str, ...],
        *,
        provider_name: str,
        limited_attr: str,
    ) -> dict[str, AbstractFallbackSelection]:
        values: dict[str, AbstractFallbackSelection] = {}
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
            cleaned, evidence = self._clean_candidate(
                value,
                doi=doi,
                provider_name=provider_name,
            )
            values[doi] = AbstractFallbackSelection(
                abstract=cleaned,
                source=self._source(provider_name) if cleaned else "",
                evidence=(evidence,) if evidence is not None else (),
            )
        return values

    def _provider_values_many(
        self,
        provider: AbstractProvider | None,
        dois: tuple[str, ...],
        *,
        provider_name: str,
        limited_attr: str,
    ) -> dict[str, AbstractFallbackSelection]:
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

        result: dict[str, AbstractFallbackSelection] = {}
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
                    cleaned, evidence = self._clean_candidate(
                        value,
                        doi=doi,
                        provider_name=provider_name,
                    )
                    result[doi] = AbstractFallbackSelection(
                        abstract=cleaned,
                        source=self._source(provider_name) if cleaned else "",
                        evidence=(evidence,) if evidence is not None else (),
                    )
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

    def _default(self) -> str:
        result = normalize_provider_abstract(self.unavailable_text)
        return result.normalized if result.deterministic else ""

    def select_many(
        self,
        dois: tuple[str, ...],
    ) -> dict[str, AbstractFallbackSelection]:
        """Return safe selections while retaining refused evidence per DOI."""
        normalized = tuple(dict.fromkeys(dois))
        candidates: dict[str, list[tuple[str, str]]] = {
            doi: [] for doi in normalized
        }
        evidence: dict[str, list[AbstractEvidence]] = {
            doi: [] for doi in normalized
        }

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
                selection = values.get(doi)
                if selection is None:
                    continue
                evidence[doi].extend(selection.evidence)
                if selection.abstract:
                    candidates[doi].append(
                        (selection.abstract, selection.source)
                    )

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
                cleaned, refused = self._clean_candidate(
                    value,
                    doi=doi,
                    provider_name="Mendeley",
                )
                if refused is not None:
                    evidence[doi].append(refused)
                if cleaned:
                    candidates[doi].append((cleaned, "mendeley"))

        default = self._default()
        result: dict[str, AbstractFallbackSelection] = {}
        for doi in normalized:
            if candidates[doi]:
                abstract, source = max(
                    candidates[doi],
                    key=lambda item: len(item[0]),
                )
            else:
                abstract, source = default, ""
            result[doi] = AbstractFallbackSelection(
                abstract=abstract,
                source=source,
                evidence=tuple(evidence[doi]),
            )
        return result

    def abstract_many(self, dois: tuple[str, ...]) -> dict[str, str]:
        """Return the longest safe optional abstract for each DOI."""
        return {
            doi: selection.abstract
            for doi, selection in self.select_many(dois).items()
        }

    def select(self, doi: str) -> AbstractFallbackSelection:
        """Return one safe abstract selection plus refused provider evidence."""
        candidates: list[tuple[str, str]] = []
        evidence: list[AbstractEvidence] = []

        providers = (
            (
                self.semantic_scholar,
                "Semantic Scholar",
                "_semantic_scholar_limited",
                429,
            ),
            (
                self.openalex,
                "OpenAlex",
                "_openalex_limited",
                429,
            ),
        )
        for provider, provider_name, limited_attr, limited_status in providers:
            if provider is None or getattr(self, limited_attr):
                continue
            try:
                value = provider.abstract(doi)
            except HttpError as error:
                if error.status_code != limited_status:
                    raise
                setattr(self, limited_attr, True)
                self._warn_limited(provider_name)
                continue
            cleaned, refused = self._clean_candidate(
                value,
                doi=doi,
                provider_name=provider_name,
            )
            if refused is not None:
                evidence.append(refused)
            if cleaned:
                candidates.append((cleaned, self._source(provider_name)))

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
                cleaned, refused = self._clean_candidate(
                    value,
                    doi=doi,
                    provider_name="Mendeley",
                )
                if refused is not None:
                    evidence.append(refused)
                if cleaned:
                    candidates.append((cleaned, "mendeley"))

        if candidates:
            abstract, source = max(candidates, key=lambda item: len(item[0]))
        else:
            abstract, source = self._default(), ""
        return AbstractFallbackSelection(
            abstract=abstract,
            source=source,
            evidence=tuple(evidence),
        )

    def abstract(self, doi: str) -> str:
        """Return the longest safe optional abstract for one DOI."""
        return self.select(doi).abstract
