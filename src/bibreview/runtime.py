"""Application-level provider wiring for BibReview commands.

Provider adapters deliberately remain independent from environment variables and
project configuration. This module is the small composition boundary that turns
one :class:`BibReviewConfig` into concrete services for a command run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from types import MappingProxyType

from dotenv import dotenv_values

from . import __version__
from .config import BibReviewConfig, ProviderConfig
from .pipeline.collect import BibtexLookup, CitationLookup, EnrichmentLookup, WorkProvider
from .pipeline.discover import EnrichmentLookup as DiscoveryEnrichmentLookup
from .pipeline.discover import WorkProvider as DiscoveryWorkProvider
from .pipeline.enrich import EnrichmentService
from .providers.crossref import CrossRefProvider
from .providers.doi import DoiProvider
from .providers.elsevier import ElsevierProvider
from .providers.fallback import AbstractFallback
from .providers.http import HttpTransport
from .providers.ieee import IeeeProvider
from .providers.mendeley import MendeleyProvider
from .providers.openalex import OpenAlexProvider
from .providers.publisher import PublisherEnrichmentRouter
from .providers.semantic_scholar import SemanticScholarProvider
from .providers.springer import SpringerProvider
from .reporting import Reporter


@dataclass(frozen=True)
class RuntimeEnvironment:
    """Resolved runtime values plus their non-secret provenance."""

    values: Mapping[str, str]
    sources: Mapping[str, str]


@dataclass(frozen=True)
class CollectionServices:
    """Concrete collaborators required by canonical project collection."""

    provider: WorkProvider
    enrichment_lookup: EnrichmentLookup
    citation_lookup: CitationLookup
    bibtex_lookup: BibtexLookup


@dataclass(frozen=True)
class DiscoveryServices:
    """Concrete collaborators required by canonical project discovery."""

    discovery_provider: OpenAlexProvider
    provider: DiscoveryWorkProvider
    enrichment_lookup: DiscoveryEnrichmentLookup


@dataclass(frozen=True)
class _CoreServices:
    transport: HttpTransport
    crossref: CrossRefProvider
    doi: DoiProvider
    enrichment: EnrichmentService


def _provider(config: BibReviewConfig, name: str) -> ProviderConfig | None:
    value = config.providers.get(name)
    return value if value is not None and value.enabled else None


def resolve_runtime_environment(
    config: BibReviewConfig,
    *,
    environ: Mapping[str, str] | None,
    reporter: Reporter,
) -> RuntimeEnvironment:
    """Compose runtime values while recording whether each value came from dotenv or process environment.

    Secret values remain private; callers such as provider diagnostics use only
    the source mapping when producing user-visible output.
    """
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    path = config.environment.file
    if path is not None:
        if path.exists():
            for key, value in dotenv_values(path).items():
                if isinstance(key, str) and isinstance(value, str):
                    values[key] = value
                    sources[key] = "dotenv"
        else:
            reporter.warning(
                f"Configured environment file does not exist: {path}; "
                "continuing with the process environment."
            )

    override = os.environ if environ is None else environ
    for key, value in override.items():
        if value is None:
            continue
        normalized_key = str(key)
        values[normalized_key] = str(value)
        sources[normalized_key] = "environment"

    return RuntimeEnvironment(
        values=MappingProxyType(values),
        sources=MappingProxyType(sources),
    )


def _runtime_environment(
    config: BibReviewConfig,
    *,
    environ: Mapping[str, str] | None,
    reporter: Reporter,
) -> Mapping[str, str]:
    """Return only resolved runtime values for provider composition."""
    return resolve_runtime_environment(
        config,
        environ=environ,
        reporter=reporter,
    ).values


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


def _optional_api_key(
    provider: ProviderConfig | None,
    *,
    provider_name: str,
    environ: Mapping[str, str],
    reporter: Reporter,
) -> str:
    """Resolve an optional provider key without disabling unauthenticated use."""
    if provider is None or not provider.api_key_env:
        return ""
    value = environ.get(provider.api_key_env, "").strip()
    if not value:
        reporter.warning(
            f"{provider_name} API key variable {provider.api_key_env} is unset; "
            "continuing without an API key."
        )
    return value


def _build_core_services(
    config: BibReviewConfig,
    *,
    reporter: Reporter,
    environ: Mapping[str, str],
) -> _CoreServices:
    crossref_config = config.providers.get("crossref")
    if crossref_config is not None and not crossref_config.enabled:
        raise ValueError("CrossRef must be enabled for DOI-backed workflows")

    user_agent = f"BibReview/{__version__}"
    transport = HttpTransport(
        reporter=reporter,
        default_headers={"User-Agent": user_agent},
    )
    crossref = CrossRefProvider(
        transport,
        mailto=config.project.contact_email,
    )
    doi = DoiProvider(transport)

    elsevier_config = _provider(config, "elsevier")
    springer_config = _provider(config, "springer")
    ieee_config = _provider(config, "ieee")

    elsevier_key = _secret(
        elsevier_config,
        field="api_key_env",
        provider_name="Elsevier",
        environ=environ,
        reporter=reporter,
    )
    springer_key = _secret(
        springer_config,
        field="api_key_env",
        provider_name="Springer",
        environ=environ,
        reporter=reporter,
    )
    ieee_key = _secret(
        ieee_config,
        field="api_key_env",
        provider_name="IEEE",
        environ=environ,
        reporter=reporter,
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

    semantic_config = _provider(config, "semantic_scholar")
    semantic_key = _optional_api_key(
        semantic_config,
        provider_name="Semantic Scholar",
        environ=environ,
        reporter=reporter,
    )
    semantic = (
        SemanticScholarProvider(transport, api_key=semantic_key)
        if semantic_config is not None
        else None
    )
    mendeley_config = _provider(config, "mendeley")
    mendeley_client_id = _secret(
        mendeley_config,
        field="client_id_env",
        provider_name="Mendeley client ID",
        environ=environ,
        reporter=reporter,
    )
    mendeley_client_secret = _secret(
        mendeley_config,
        field="client_secret_env",
        provider_name="Mendeley client secret",
        environ=environ,
        reporter=reporter,
    )
    mendeley = (
        MendeleyProvider(
            transport,
            client_id=mendeley_client_id,
            client_secret=mendeley_client_secret,
            user_agent=user_agent,
        )
        if mendeley_client_id and mendeley_client_secret
        else None
    )

    fallback = None
    if semantic is not None or mendeley is not None:
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            mendeley=mendeley,
            reporter=reporter,
        )

    return _CoreServices(
        transport=transport,
        crossref=crossref,
        doi=doi,
        enrichment=EnrichmentService(publisher=publisher, fallback=fallback),
    )


def build_collection_services(
    config: BibReviewConfig,
    *,
    reporter: Reporter | None = None,
    environ: Mapping[str, str] | None = None,
) -> CollectionServices:
    """Compose configured network providers for one collection command run."""
    progress = reporter or Reporter()
    environment = _runtime_environment(
        config,
        environ=environ,
        reporter=progress,
    )
    core = _build_core_services(config, reporter=progress, environ=environment)
    return CollectionServices(
        provider=core.crossref,
        enrichment_lookup=core.enrichment.for_collection,
        citation_lookup=core.doi.citation,
        bibtex_lookup=core.doi.bibtex,
    )


def build_discovery_services(
    config: BibReviewConfig,
    *,
    reporter: Reporter | None = None,
    environ: Mapping[str, str] | None = None,
) -> DiscoveryServices:
    """Compose configured OpenAlex/CrossRef services for one discovery run."""
    progress = reporter or Reporter()
    environment = _runtime_environment(
        config,
        environ=environ,
        reporter=progress,
    )
    if config.discovery.provider != "openalex":
        raise ValueError(f"unsupported discovery provider: {config.discovery.provider}")

    openalex_config = config.providers.get("openalex")
    if openalex_config is not None and not openalex_config.enabled:
        raise ValueError("OpenAlex must be enabled when selected for discovery")

    core = _build_core_services(config, reporter=progress, environ=environment)
    openalex_key = _optional_api_key(
        openalex_config,
        provider_name="OpenAlex",
        environ=environment,
        reporter=progress,
    )
    return DiscoveryServices(
        discovery_provider=OpenAlexProvider(core.transport, api_key=openalex_key),
        provider=core.crossref,
        enrichment_lookup=core.enrichment.for_discovery,
    )
