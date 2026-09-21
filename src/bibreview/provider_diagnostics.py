"""Safe diagnostics for configured BibReview metadata providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

from .config import BibReviewConfig, ProviderConfig
from .providers.http import HttpError, HttpTransport
from .runtime import RuntimeEnvironment, resolve_runtime_environment
from .reporting import Reporter


@dataclass(frozen=True)
class ProviderDiagnostic:
    """One provider configuration/runtime diagnostic without secret values."""

    name: str
    enabled: bool
    credential_variable: str
    credential_source: str
    status: str
    detail: str
    checked: bool = False

    def data(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return asdict(self)


@dataclass(frozen=True)
class _ProviderSpec:
    credential_field: str = ""
    credential_required: bool = False


_PROVIDER_ORDER = (
    "crossref",
    "openalex",
    "elsevier",
    "springer",
    "ieee",
    "semantic_scholar",
    "mendeley",
)

_PROVIDER_SPECS = {
    "crossref": _ProviderSpec(),
    "openalex": _ProviderSpec("api_key_env", False),
    "elsevier": _ProviderSpec("api_key_env", True),
    "springer": _ProviderSpec("api_key_env", True),
    "ieee": _ProviderSpec("api_key_env", True),
    "semantic_scholar": _ProviderSpec("api_key_env", False),
    "mendeley": _ProviderSpec("token_env", True),
}


def _provider_config(config: BibReviewConfig, name: str) -> ProviderConfig | None:
    return config.providers.get(name)


def _enabled(config: BibReviewConfig, name: str) -> bool:
    configured = _provider_config(config, name)
    if name == "crossref":
        return configured is None or configured.enabled
    if name == "openalex":
        if config.discovery.provider != "openalex":
            return configured is not None and configured.enabled
        return configured is None or configured.enabled
    return configured is not None and configured.enabled


def _credential(
    config: BibReviewConfig,
    environment: RuntimeEnvironment,
    name: str,
) -> tuple[str, str, str]:
    """Return variable name, source, and resolved value without exposing it."""
    spec = _PROVIDER_SPECS[name]
    if not spec.credential_field:
        return "", "not-required", ""
    provider = _provider_config(config, name)
    variable = (
        getattr(provider, spec.credential_field)
        if provider is not None
        else ""
    )
    if not variable:
        return "", "not-configured", ""
    value = environment.values.get(variable, "").strip()
    if not value:
        return variable, "missing", ""
    return variable, environment.sources.get(variable, "environment"), value


def _static_status(
    *,
    enabled: bool,
    credential_variable: str,
    credential_source: str,
    spec: _ProviderSpec,
) -> tuple[str, str]:
    if not enabled:
        return "disabled", "provider disabled by project configuration"
    if not spec.credential_field:
        return "configured", "no credential required"
    if credential_source == "not-configured":
        if spec.credential_required:
            return (
                "configuration-error",
                "enabled provider requires a configured credential variable",
            )
        return "configured", "optional credential is not configured"
    if credential_source == "missing":
        if spec.credential_required:
            return "missing-credential", "configured credential variable is unset"
        return "configured", "optional credential is unset; unauthenticated access will be used"
    return "configured", f"credential loaded from {credential_source}"


def _probe_crossref(transport: HttpTransport, config: BibReviewConfig, _: str) -> None:
    params: dict[str, object] = {"rows": 0}
    if config.project.contact_email:
        params["mailto"] = config.project.contact_email
    transport.json(
        "https://api.crossref.org/works",
        params=params,
        context="CrossRef provider diagnostic",
    )


def _probe_openalex(transport: HttpTransport, _: BibReviewConfig, credential: str) -> None:
    params: dict[str, object] = {"per-page": 1}
    if credential:
        params["api_key"] = credential
    transport.json(
        "https://api.openalex.org/works",
        params=params,
        context="OpenAlex provider diagnostic",
    )


def _probe_elsevier(transport: HttpTransport, _: BibReviewConfig, credential: str) -> None:
    transport.json(
        "https://api.elsevier.com/content/search/scopus",
        params={"query": "TITLE(test)", "count": 1},
        headers={"Accept": "application/json", "X-ELS-APIKey": credential},
        context="Elsevier provider diagnostic",
    )


def _probe_springer(transport: HttpTransport, _: BibReviewConfig, credential: str) -> None:
    transport.json(
        "https://api.springernature.com/meta/v2/json",
        params={"q": "keyword:test", "p": 1, "api_key": credential},
        context="Springer provider diagnostic",
    )


def _probe_ieee(transport: HttpTransport, _: BibReviewConfig, credential: str) -> None:
    transport.json(
        "https://ieeexploreapi.ieee.org/api/v1/search/articles",
        params={
            "apikey": credential,
            "querytext": "test",
            "max_records": 1,
            "format": "json",
        },
        context="IEEE provider diagnostic",
    )


def _probe_semantic_scholar(
    transport: HttpTransport,
    _: BibReviewConfig,
    credential: str,
) -> None:
    headers = {"x-api-key": credential} if credential else None
    transport.json(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={"query": "test", "limit": 1, "fields": "title"},
        headers=headers,
        context="Semantic Scholar provider diagnostic",
    )


def _probe_mendeley(transport: HttpTransport, _: BibReviewConfig, credential: str) -> None:
    transport.json(
        "https://api.mendeley.com/catalog",
        params={"doi": "10.1016/j.molcel.2009.09.013", "view": "bib"},
        headers={
            "Accept": "application/vnd.mendeley-document.1+json",
            "Authorization": f"Bearer {credential}",
        },
        context="Mendeley provider diagnostic",
    )


_PROBES: dict[str, Callable[[HttpTransport, BibReviewConfig, str], None]] = {
    "crossref": _probe_crossref,
    "openalex": _probe_openalex,
    "elsevier": _probe_elsevier,
    "springer": _probe_springer,
    "ieee": _probe_ieee,
    "semantic_scholar": _probe_semantic_scholar,
    "mendeley": _probe_mendeley,
}


def _failure_status(name: str, error: HttpError) -> tuple[str, str]:
    status = error.status_code
    if status == 401:
        if name == "mendeley":
            return (
                "authentication-failed",
                "OAuth bearer access token rejected; Mendeley catalog access requires OAuth 2.0",
            )
        return "authentication-failed", "credential rejected by provider"
    if status == 403:
        return (
            "access-denied",
            "provider denied access; check credential permissions and service entitlements",
        )
    if status == 429:
        return "rate-limited", "provider rate limit reached after retries"
    if isinstance(status, int) and 500 <= status <= 599:
        return "unavailable", f"provider returned HTTP {status}"
    if status is None:
        return "unavailable", str(error)
    return "request-failed", f"provider returned HTTP {status}"


def diagnose_providers(
    config: BibReviewConfig,
    *,
    check: bool = False,
    environ: dict[str, str] | None = None,
    reporter: Reporter | None = None,
    transport: HttpTransport | None = None,
) -> tuple[ProviderDiagnostic, ...]:
    """Inspect provider configuration and optionally perform safe live probes."""
    progress = reporter or Reporter(-1)
    environment = resolve_runtime_environment(
        config,
        environ=environ,
        reporter=progress,
    )
    live_transport = transport or HttpTransport(reporter=progress)

    result: list[ProviderDiagnostic] = []
    for name in _PROVIDER_ORDER:
        enabled = _enabled(config, name)
        spec = _PROVIDER_SPECS[name]
        variable, source, value = _credential(config, environment, name)
        status, detail = _static_status(
            enabled=enabled,
            credential_variable=variable,
            credential_source=source,
            spec=spec,
        )
        checked = False

        can_check = enabled and status == "configured"
        if check and can_check:
            checked = True
            try:
                _PROBES[name](live_transport, config, value)
            except HttpError as error:
                status, detail = _failure_status(name, error)
            except (OSError, ValueError, TypeError):
                status = "unavailable"
                detail = "provider diagnostic failed without exposing provider response data"
            else:
                status = "available"
                detail = "live provider check succeeded"

        result.append(
            ProviderDiagnostic(
                name=name,
                enabled=enabled,
                credential_variable=variable,
                credential_source=source,
                status=status,
                detail=detail,
                checked=checked,
            )
        )

    return tuple(result)


def format_provider_diagnostics(items: tuple[ProviderDiagnostic, ...]) -> str:
    """Format diagnostics as a compact human-readable table."""
    if not items:
        return "No providers configured."
    headers = ("Provider", "Credential", "Source", "Status")
    rows = [
        (
            item.name,
            item.credential_variable or "-",
            item.credential_source,
            item.status,
        )
        for item in items
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    lines = [
        "  ".join(headers[index].ljust(widths[index]) for index in range(len(headers))),
        "  ".join("-" * width for width in widths),
    ]
    for item, row in zip(items, rows, strict=True):
        lines.append(
            "  ".join(row[index].ljust(widths[index]) for index in range(len(headers)))
        )
        lines.append(f"  {item.detail}")
    return "\n".join(lines)
