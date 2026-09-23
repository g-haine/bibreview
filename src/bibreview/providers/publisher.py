"""Route DOI landing pages to configured publisher enrichment providers."""

from __future__ import annotations

from urllib.parse import urlsplit

from ..reporting import Reporter
from .base import Enrichment, EnrichmentProvider
from .doi import DoiProvider
from .http import HttpError


class PublisherEnrichmentRouter:
    """Select a publisher provider from the DOI landing host.

    Provider availability is explicit: a recognized publisher with no configured
    provider simply yields no enrichment. No environment variables or project
    configuration are read here.
    """

    def __init__(
        self,
        doi_provider: DoiProvider,
        *,
        elsevier: EnrichmentProvider | None = None,
        springer: EnrichmentProvider | None = None,
        ieee: EnrichmentProvider | None = None,
        reporter: Reporter | None = None,
    ) -> None:
        self.doi_provider = doi_provider
        self.elsevier = elsevier
        self.springer = springer
        self.ieee = ieee
        self.reporter = reporter or Reporter()
        self._disabled: set[str] = set()

    def enrich(self, doi: str) -> Enrichment:
        try:
            landing = self.doi_provider.landing_url(doi)
        except HttpError:
            self.reporter.warning(
                f"Publisher lookup HTTP failure for {doi}; "
                "skipping optional publisher enrichment for this publication."
            )
            return Enrichment()

        if not landing:
            return Enrichment()

        host = (urlsplit(landing).hostname or "").lower()
        provider: EnrichmentProvider | None = None
        provider_name = ""
        if "elsevier" in host or "sciencedirect" in host:
            provider = self.elsevier
            provider_name = "Elsevier"
        elif "springer" in host:
            provider = self.springer
            provider_name = "Springer"
        elif "ieee" in host:
            provider = self.ieee
            provider_name = "IEEE"

        if provider is None or provider_name in self._disabled:
            return Enrichment()

        try:
            return provider.enrich(doi)
        except HttpError as error:
            if error.status_code in {401, 403, 429}:
                self._disabled.add(provider_name)
                self.reporter.warning(
                    f"{provider_name} HTTP {error.status_code}; skipping this optional "
                    "publisher provider for the rest of this run. Other configured "
                    "abstract fallbacks will still be tried."
                )
            else:
                status = (
                    f"HTTP {error.status_code}"
                    if error.status_code is not None
                    else "HTTP/response failure"
                )
                self.reporter.warning(
                    f"{provider_name} {status} for {doi}; skipping optional publisher "
                    "enrichment for this publication. Other configured abstract "
                    "fallbacks will still be tried."
                )
            return Enrichment()
