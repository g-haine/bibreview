"""Route DOI landing pages to configured publisher enrichment providers."""

from __future__ import annotations

from urllib.parse import urlsplit

from .base import Enrichment, EnrichmentProvider
from .doi import DoiProvider


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
    ) -> None:
        self.doi_provider = doi_provider
        self.elsevier = elsevier
        self.springer = springer
        self.ieee = ieee

    def enrich(self, doi: str) -> Enrichment:
        landing = self.doi_provider.landing_url(doi)
        if not landing:
            return Enrichment()
        host = (urlsplit(landing).hostname or "").lower()
        provider: EnrichmentProvider | None = None
        if "elsevier" in host or "sciencedirect" in host:
            provider = self.elsevier
        elif "springer" in host:
            provider = self.springer
        elif "ieee" in host:
            provider = self.ieee
        return provider.enrich(doi) if provider is not None else Enrichment()
