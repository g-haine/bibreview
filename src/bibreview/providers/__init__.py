"""Built-in bibliographic provider adapters."""

from .base import Enrichment, EnrichmentProvider
from .crossref import CrossRefError, CrossRefProvider
from .doi import DoiProvider, format_bibtex
from .elsevier import ElsevierProvider
from .fallback import AbstractFallback, AbstractProvider
from .http import HttpError, HttpTransport
from .ieee import IeeeProvider
from .mendeley import MendeleyProvider, mendeley_abstract
from .openalex import OpenAlexError, OpenAlexProvider
from .publisher import PublisherEnrichmentRouter
from .semantic_scholar import SemanticScholarProvider
from .springer import SpringerProvider

__all__ = [
    "AbstractFallback",
    "AbstractProvider",
    "CrossRefError",
    "CrossRefProvider",
    "DoiProvider",
    "ElsevierProvider",
    "Enrichment",
    "EnrichmentProvider",
    "HttpError",
    "HttpTransport",
    "IeeeProvider",
    "MendeleyProvider",
    "OpenAlexError",
    "OpenAlexProvider",
    "PublisherEnrichmentRouter",
    "SemanticScholarProvider",
    "SpringerProvider",
    "format_bibtex",
    "mendeley_abstract",
]
