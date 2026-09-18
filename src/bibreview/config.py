"""Project configuration loading and validation for BibReview."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

import yaml

SCHEMA_VERSION = 1
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DEFAULT_DISCOVERY_TYPES = (
    "journal-article",
    "proceedings-article",
    "book-chapter",
    "book",
    "monograph",
)
REFRESHABLE_PUBLICATION_FIELDS = frozenset({
    "title",
    "abstract",
    "container_title",
    "publication_year",
    "volume",
    "issue",
    "pages",
    "publisher",
    "event",
})


class ConfigError(ValueError):
    """Raised when a BibReview project configuration is invalid."""


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _string(value: Any, name: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise ConfigError(f"{name} is required")
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise ConfigError(f"{name} must not be empty")
    return value


def _string_tuple(
    value: Any,
    name: str,
    *,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value, 1):
        cleaned = item.strip()
        if not cleaned:
            raise ConfigError(f"{name}[{index}] must not be empty")
        if cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return tuple(result)


def _boolean(value: Any, name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")
    return value


def _integer(value: Any, name: str, default: int, *, minimum: int = 1) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConfigError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True)
class EnvironmentConfig:
    """Optional runtime environment-file configuration."""

    file: Path | None = None


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    slug: str
    title: str = ""
    description: str = ""
    repository: str = ""
    contact_name: str = ""


@dataclass(frozen=True)
class PathsConfig:
    bibliography: Path
    collected: Path
    author_mappings: Path
    known: Path
    pending: Path
    rejected: Path
    review: Path
    bibtex: Path
    archive: Path
    site: Path


@dataclass(frozen=True)
class DiscoveryConfig:
    provider: str = "openalex"
    query: str = ""
    max_pages: int = 20
    accepted_types: tuple[str, ...] = DEFAULT_DISCOVERY_TYPES
    exclude_doi_substrings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RefreshConfig:
    """Policy selecting existing publications eligible for refresh checks."""

    types: tuple[str, ...] = ()
    when_missing_any: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelevanceConfig:
    patterns: tuple[str, ...] = ()
    unmatched: str = "manual-review"


@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool = True
    api_key_env: str = ""
    token_env: str = ""


@dataclass(frozen=True)
class SiteBrandingConfig:
    logo: str = ""
    favicon: str = ""


@dataclass(frozen=True)
class SiteConfig:
    enabled: bool = True
    implementation: str = "jekyll"
    template: str = "default"
    source: Path | None = None
    url: str = ""
    baseurl: str = ""
    search: bool = True
    branding: SiteBrandingConfig = SiteBrandingConfig()


@dataclass(frozen=True)
class BibReviewConfig:
    source: Path
    schema_version: int
    environment: EnvironmentConfig
    project: ProjectConfig
    paths: PathsConfig
    discovery: DiscoveryConfig
    refresh: RefreshConfig
    relevance: RelevanceConfig
    providers: Mapping[str, ProviderConfig]
    site: SiteConfig


def _path(base: Path, value: Any, default: str, name: str) -> Path:
    raw = _string(value, name) or default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _optional_path(base: Path, value: Any, name: str) -> Path | None:
    raw = _string(value, name)
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: str | Path = "bibreview.yml") -> BibReviewConfig:
    """Load and validate a BibReview schema-v1 project configuration."""
    source = Path(path).expanduser().resolve()
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read configuration {source}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in {source}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a mapping")

    schema_version = raw.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION}"
        )

    base = source.parent
    environment_raw = _mapping(raw.get("environment"), "environment")
    environment = EnvironmentConfig(
        file=_optional_path(base, environment_raw.get("file"), "environment.file"),
    )

    project_raw = _mapping(raw.get("project"), "project")
    name = _string(project_raw.get("name"), "project.name", required=True)
    slug = _string(project_raw.get("slug"), "project.slug", required=True)
    if not _SLUG.fullmatch(slug):
        raise ConfigError(
            "project.slug must contain lowercase ASCII letters/digits separated by single hyphens"
        )
    contact = _mapping(project_raw.get("contact"), "project.contact")
    project = ProjectConfig(
        name=name,
        slug=slug,
        title=_string(project_raw.get("title"), "project.title"),
        description=_string(project_raw.get("description"), "project.description"),
        repository=_string(project_raw.get("repository"), "project.repository"),
        contact_name=_string(contact.get("name"), "project.contact.name"),
    )

    paths_raw = _mapping(raw.get("paths"), "paths")
    paths = PathsConfig(
        bibliography=_path(base, paths_raw.get("bibliography"), "data/bibliography.json", "paths.bibliography"),
        collected=_path(base, paths_raw.get("collected"), "data/collected.json", "paths.collected"),
        author_mappings=_path(base, paths_raw.get("author_mappings"), "data/authors.json", "paths.author_mappings"),
        known=_path(base, paths_raw.get("known"), "data/known.txt", "paths.known"),
        pending=_path(base, paths_raw.get("pending"), "data/pending.txt", "paths.pending"),
        rejected=_path(base, paths_raw.get("rejected"), "data/rejected.txt", "paths.rejected"),
        review=_path(base, paths_raw.get("review"), "data/review.txt", "paths.review"),
        bibtex=_path(base, paths_raw.get("bibtex"), "bib", "paths.bibtex"),
        archive=_path(base, paths_raw.get("archive"), "archive", "paths.archive"),
        site=_path(base, paths_raw.get("site"), "site", "paths.site"),
    )

    discovery_raw = _mapping(raw.get("discovery"), "discovery")
    discovery = DiscoveryConfig(
        provider=_string(discovery_raw.get("provider"), "discovery.provider") or "openalex",
        query=_string(discovery_raw.get("query"), "discovery.query"),
        max_pages=_integer(discovery_raw.get("max_pages"), "discovery.max_pages", 20),
        accepted_types=_string_tuple(
            discovery_raw.get("accepted_types"),
            "discovery.accepted_types",
            default=DEFAULT_DISCOVERY_TYPES,
        ),
        exclude_doi_substrings=_string_tuple(
            discovery_raw.get("exclude_doi_substrings"),
            "discovery.exclude_doi_substrings",
        ),
    )

    refresh_raw = _mapping(raw.get("refresh"), "refresh")
    refresh_fields = _string_tuple(
        refresh_raw.get("when_missing_any"),
        "refresh.when_missing_any",
    )
    unknown_refresh_fields = set(refresh_fields) - REFRESHABLE_PUBLICATION_FIELDS
    if unknown_refresh_fields:
        raise ConfigError(
            "refresh.when_missing_any contains unsupported publication field(s): "
            + ", ".join(sorted(unknown_refresh_fields))
        )
    refresh = RefreshConfig(
        types=_string_tuple(refresh_raw.get("types"), "refresh.types"),
        when_missing_any=refresh_fields,
    )

    relevance_raw = _mapping(raw.get("relevance"), "relevance")
    patterns_raw = relevance_raw.get("patterns", [])
    if not isinstance(patterns_raw, list) or any(not isinstance(v, str) for v in patterns_raw):
        raise ConfigError("relevance.patterns must be a list of strings")
    patterns = tuple(patterns_raw)
    for index, pattern in enumerate(patterns, 1):
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            raise ConfigError(f"invalid relevance.patterns[{index}]: {error}") from error
    unmatched = _string(relevance_raw.get("unmatched"), "relevance.unmatched") or "manual-review"
    if unmatched not in {"manual-review", "reject"}:
        raise ConfigError("relevance.unmatched must be 'manual-review' or 'reject'")
    relevance = RelevanceConfig(patterns=patterns, unmatched=unmatched)

    providers_raw = _mapping(raw.get("providers"), "providers")
    providers: dict[str, ProviderConfig] = {}
    for provider_name, provider_value in providers_raw.items():
        if not isinstance(provider_name, str) or not provider_name:
            raise ConfigError("provider names must be non-empty strings")
        item = _mapping(provider_value, f"providers.{provider_name}")
        providers[provider_name] = ProviderConfig(
            enabled=_boolean(item.get("enabled"), f"providers.{provider_name}.enabled", True),
            api_key_env=_string(item.get("api_key_env"), f"providers.{provider_name}.api_key_env"),
            token_env=_string(item.get("token_env"), f"providers.{provider_name}.token_env"),
        )

    site_raw = _mapping(raw.get("site"), "site")
    branding_raw = _mapping(site_raw.get("branding"), "site.branding")
    site = SiteConfig(
        enabled=_boolean(site_raw.get("enabled"), "site.enabled", True),
        implementation=_string(site_raw.get("implementation"), "site.implementation") or "jekyll",
        template=_string(site_raw.get("template"), "site.template") or "default",
        source=_path(base, site_raw.get("source"), str(paths.site), "site.source"),
        url=_string(site_raw.get("url"), "site.url"),
        baseurl=_string(site_raw.get("baseurl"), "site.baseurl"),
        search=_boolean(site_raw.get("search"), "site.search", True),
        branding=SiteBrandingConfig(
            logo=_string(branding_raw.get("logo"), "site.branding.logo"),
            favicon=_string(branding_raw.get("favicon"), "site.branding.favicon"),
        ),
    )

    return BibReviewConfig(
        source=source,
        schema_version=schema_version,
        environment=environment,
        project=project,
        paths=paths,
        discovery=discovery,
        refresh=refresh,
        relevance=relevance,
        providers=MappingProxyType(providers),
        site=site,
    )
