"""Built-in bibliographic provider adapters."""

from .base import Enrichment, EnrichmentProvider
from .crossref import CrossRefError, CrossRefProvider
from .doi import DoiProvider, format_bibtex
from .elsevier import ElsevierProvider
from .http import HttpError, HttpTransport
from .ieee import IeeeProvider
from .publisher import PublisherEnrichmentRouter
from .springer import SpringerProvider

__all__ = [
    "CrossRefError",
    "CrossRefProvider",
    "DoiProvider",
    "ElsevierProvider",
    "Enrichment",
    "EnrichmentProvider",
    "HttpError",
    "HttpTransport",
    "IeeeProvider",
    "PublisherEnrichmentRouter",
    "SpringerProvider",
    "format_bibtex",
]
