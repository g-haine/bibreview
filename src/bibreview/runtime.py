"""Application-level provider wiring for BibReview commands.

Provider adapters deliberately remain independent from environment variables and
project configuration. This module is the small composition boundary that turns
one :class:`BibReviewConfig` into concrete services for a command run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os

from . import __version__
from .config import BibReviewConfig, ProviderConfig
from .pipeline.collect import BibtexLookup, CitationLookup, EnrichmentLookup, WorkProvider
from .pipeline.enrich import EnrichmentService
from .providers.crossref import CrossRefProvider
from .providers.doi import DoiProvider
from .providers.elsevier import ElsevierProvider
from .providers.fallback import AbstractFallback
from .providers.http import HttpTransport
from .providers.ieee import IeeeProvider
from .providers.mendeley import MendeleyProvider
from .providers.publisher import PublisherEnrichmentRouter
from .providers.semantic_scholar import SemanticScholarProvider
from .providers.springer import SpringerProvider
from .reporting import Reporter


@dataclass(frozen=True)
class CollectionServices:
    """Concrete collaborators required by canonical project collection."""

    provider: WorkProvider
    enrichment_lookup: EnrichmentLookup
    citation_lookup: CitationLookup
    bibtex_lookup: BibtexLookup


def _provider(config: BibReviewConfig, name: str) -> ProviderConfig | None:
    value = config.providers.get(name)
    return value if value is not None and value.enabled else None


def _secret(
    provider: ProviderConfig | None,
    *,
    field: str,
    provider_name: str,
    environ: Mapping[str, str],
    reporter: Reporter,
) -> str | None:
    if provider is None:
        return None
    env_name = getattr(provider, field)
    if not env_name:
        reporter.warning(
            f"{provider_name} is enabled but no environment-variable name is configured; "
            "skipping this optional provider."
        )
        return None
    value = environ.get(env_name, "").strip()
    if not value:
        reporter.warning(
            f"{provider_name} is enabled but {env_name} is unset; "
            "skipping this optional provider."
        )
        return None
    return value


def build_collection_services(
    config: BibReviewConfig,
    *,
    reporter: Reporter | None = None,
    environ: Mapping[str, str] | None = None,
) -> CollectionServices:
    """Compose configured network providers for one collection command run."""
    progress = reporter or Reporter()
    environment = os.environ if environ is None else environ

    crossref_config = config.providers.get("crossref")
    if crossref_config is not None and not crossref_config.enabled:
        raise ValueError("CrossRef must be enabled for DOI-backed collection")

    user_agent = f"BibReview/{__version__}"
    transport = HttpTransport(
        reporter=progress,
        default_headers={"User-Agent": user_agent},
    )
    crossref = CrossRefProvider(transport)
    doi = DoiProvider(transport)

    elsevier_config = _provider(config, "elsevier")
    springer_config = _provider(config, "springer")
    ieee_config = _provider(config, "ieee")

    elsevier_key = _secret(
        elsevier_config,
        field="api_key_env",
        provider_name="Elsevier",
        environ=environment,
        reporter=progress,
    )
    springer_key = _secret(
        springer_config,
        field="api_key_env",
        provider_name="Springer",
        environ=environment,
        reporter=progress,
    )
    ieee_key = _secret(
        ieee_config,
        field="api_key_env",
        provider_name="IEEE",
        environ=environment,
        reporter=progress,
    )

    elsevier = ElsevierProvider(transport, api_key=elsevier_key) if elsevier_key else None
    springer = SpringerProvider(transport, api_key=springer_key) if springer_key else None
    ieee = IeeeProvider(transport, api_key=ieee_key) if ieee_key else None

    publisher = None
    if any(provider is not None for provider in (elsevier, springer, ieee)):
        publisher = PublisherEnrichmentRouter(
            doi,
            elsevier=elsevier,
            springer=springer,
            ieee=ieee,
        )

    semantic = (
        SemanticScholarProvider(transport)
        if _provider(config, "semantic_scholar") is not None
        else None
    )
    mendeley_config = _provider(config, "mendeley")
    mendeley_token = _secret(
        mendeley_config,
        field="token_env",
        provider_name="Mendeley",
        environ=environment,
        reporter=progress,
    )
    mendeley = (
        MendeleyProvider(transport, token=mendeley_token, user_agent=user_agent)
        if mendeley_token
        else None
    )

    fallback = None
    if semantic is not None or mendeley is not None:
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            mendeley=mendeley,
            reporter=progress,
        )

    enrichment = EnrichmentService(publisher=publisher, fallback=fallback)
    return CollectionServices(
        provider=crossref,
        enrichment_lookup=enrichment.for_collection,
        citation_lookup=doi.citation,
        bibtex_lookup=doi.bibtex,
    )
