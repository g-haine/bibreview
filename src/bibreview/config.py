"""Project configuration loading and validation for BibReview."""

from __future__ import annotations

from dataclasses import dataclass, field
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


def _number(
    value: Any,
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
) -> float:
    if value is None:
        return default
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or float(value) < minimum
    ):
        raise ConfigError(f"{name} must be a number >= {minimum}")
    return float(value)


def _text(value: Any, name: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    return value


def _string_mapping(value: Any, name: str) -> Mapping[str, str]:
    raw = _mapping(value, name)
    result: dict[str, str] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError(f"{name} keys must be non-empty strings")
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{name}.{key} must be a non-empty string")
        result[key.strip()] = item.strip()
    return MappingProxyType(result)


def _event_category_rules(value: Any, name: str) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list")
    result: list[tuple[str, str]] = []
    for index, raw_rule in enumerate(value, 1):
        rule = _mapping(raw_rule, f"{name}[{index}]")
        unknown = set(rule) - {"pattern", "category"}
        if unknown:
            raise ConfigError(
                f"{name}[{index}] contains unsupported field(s): "
                + ", ".join(sorted(unknown))
            )
        pattern = _string(rule.get("pattern"), f"{name}[{index}].pattern", required=True)
        category = _string(
            rule.get("category"),
            f"{name}[{index}].category",
            required=True,
        )
        try:
            re.compile(pattern)
        except re.error as error:
            raise ConfigError(
                f"invalid {name}[{index}].pattern: {error}"
            ) from error
        result.append((pattern, category))
    return tuple(result)


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
    contact_email: str = ""


@dataclass(frozen=True)
class ArxivConfig:
    """Optional arXiv feed-cache configuration."""

    enabled: bool = False
    query: str = ""
    max_results: int = 25
    sort_by: str = "lastUpdatedDate"
    sort_order: str = "descending"
    output: Path | None = None


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
class AuditConfig:
    """Persistent state and batching policy for non-destructive audits."""

    campaign: Path
    report: Path
    batch_size: int = 50


@dataclass(frozen=True)
class ReferencesConfig:
    """Persistent state and batching policy for reference refresh."""

    campaign: Path
    report: Path
    batch_size: int = 50


@dataclass(frozen=True)
class RelevanceConfig:
    patterns: tuple[str, ...] = ()
    unmatched: str = "manual-review"


@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool = True
    api_key_env: str = ""
    token_env: str = ""
    client_id_env: str = ""
    client_secret_env: str = ""
    min_interval_seconds: float = 0.0


@dataclass(frozen=True)
class SiteBrandingConfig:
    logo: str = ""
    favicon: str = ""


@dataclass(frozen=True)
class JekyllSiteConfig:
    """Project-specific presentation policy for BibReview's Jekyll renderer."""

    baseurl_expression: str = "{{ site.baseurl }}"
    count_posts_include: str = "{% include count-posts.html %}"
    author_index_extra_html: str = ""
    include_authorless_year_publications: bool = True
    date_timezone: str = "+0100"
    author_path_prefix: str = "authors"
    bibtex_asset_prefix: str = "assets/bib"
    category_by_type: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    event_category_rules: tuple[tuple[str, str], ...] = ()
    isbn_types: tuple[str, ...] = ("book", "monograph")
    keyword_joiner: str = ", "
    tag_delimiter: str = ";"


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
    jekyll: JekyllSiteConfig = field(default_factory=JekyllSiteConfig)


@dataclass(frozen=True)
class BibReviewConfig:
    source: Path
    schema_version: int
    environment: EnvironmentConfig
    project: ProjectConfig
    arxiv: ArxivConfig
    paths: PathsConfig
    discovery: DiscoveryConfig
    refresh: RefreshConfig
    audit: AuditConfig
    references: ReferencesConfig
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
        contact_email=_string(contact.get("email"), "project.contact.email"),
    )

    arxiv_raw = _mapping(raw.get("arxiv"), "arxiv")
    arxiv_sort_by = (
        _string(arxiv_raw.get("sort_by"), "arxiv.sort_by")
        or "lastUpdatedDate"
    )
    if arxiv_sort_by not in {"relevance", "lastUpdatedDate", "submittedDate"}:
        raise ConfigError(
            "arxiv.sort_by must be 'relevance', 'lastUpdatedDate', or "
            "'submittedDate'"
        )
    arxiv_sort_order = (
        _string(arxiv_raw.get("sort_order"), "arxiv.sort_order")
        or "descending"
    )
    if arxiv_sort_order not in {"ascending", "descending"}:
        raise ConfigError(
            "arxiv.sort_order must be 'ascending' or 'descending'"
        )
    arxiv = ArxivConfig(
        enabled=_boolean(arxiv_raw.get("enabled"), "arxiv.enabled", False),
        query=_string(arxiv_raw.get("query"), "arxiv.query"),
        max_results=_integer(
            arxiv_raw.get("max_results"),
            "arxiv.max_results",
            25,
        ),
        sort_by=arxiv_sort_by,
        sort_order=arxiv_sort_order,
        output=_optional_path(
            base,
            arxiv_raw.get("output"),
            "arxiv.output",
        ),
    )
    if arxiv.enabled:
        if not arxiv.query:
            raise ConfigError("arxiv.query is required when arxiv is enabled")
        if arxiv.output is None:
            raise ConfigError("arxiv.output is required when arxiv is enabled")
        if not project.contact_email:
            raise ConfigError(
                "project.contact.email is required when arxiv is enabled"
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

    audit_raw = _mapping(raw.get("audit"), "audit")
    audit = AuditConfig(
        campaign=_path(
            base,
            audit_raw.get("campaign"),
            "data/audit/campaign.json",
            "audit.campaign",
        ),
        report=_path(
            base,
            audit_raw.get("report"),
            "data/audit/report.json",
            "audit.report",
        ),
        batch_size=_integer(
            audit_raw.get("batch_size"),
            "audit.batch_size",
            50,
        ),
    )
    if audit.campaign == audit.report:
        raise ConfigError("audit.campaign and audit.report must be different paths")

    references_raw = _mapping(raw.get("references"), "references")
    references = ReferencesConfig(
        campaign=_path(
            base,
            references_raw.get("campaign"),
            "audit/references/campaign.json",
            "references.campaign",
        ),
        report=_path(
            base,
            references_raw.get("report"),
            "audit/references/report.json",
            "references.report",
        ),
        batch_size=_integer(
            references_raw.get("batch_size"),
            "references.batch_size",
            50,
        ),
    )
    if references.campaign == references.report:
        raise ConfigError(
            "references.campaign and references.report must be different paths"
        )

    references_reserved_paths = {
        paths.bibliography,
        paths.collected,
        paths.author_mappings,
        paths.known,
        paths.pending,
        paths.rejected,
        paths.review,
        audit.campaign,
        audit.report,
    }
    for name, path in (
        ("references.campaign", references.campaign),
        ("references.report", references.report),
    ):
        if path in references_reserved_paths:
            raise ConfigError(
                f"{name} must not overlap canonical/project/audit state paths"
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
            client_id_env=_string(
                item.get("client_id_env"),
                f"providers.{provider_name}.client_id_env",
            ),
            client_secret_env=_string(
                item.get("client_secret_env"),
                f"providers.{provider_name}.client_secret_env",
            ),
            min_interval_seconds=_number(
                item.get("min_interval_seconds"),
                f"providers.{provider_name}.min_interval_seconds",
                0.0,
                minimum=0.0,
            ),
        )

    site_raw = _mapping(raw.get("site"), "site")
    branding_raw = _mapping(site_raw.get("branding"), "site.branding")
    jekyll_raw = _mapping(site_raw.get("jekyll"), "site.jekyll")
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
        jekyll=JekyllSiteConfig(
            baseurl_expression=_text(
                jekyll_raw.get("baseurl_expression"),
                "site.jekyll.baseurl_expression",
                "{{ site.baseurl }}",
            ),
            count_posts_include=_text(
                jekyll_raw.get("count_posts_include"),
                "site.jekyll.count_posts_include",
                "{% include count-posts.html %}",
            ),
            author_index_extra_html=_text(
                jekyll_raw.get("author_index_extra_html"),
                "site.jekyll.author_index_extra_html",
            ),
            include_authorless_year_publications=_boolean(
                jekyll_raw.get("include_authorless_year_publications"),
                "site.jekyll.include_authorless_year_publications",
                True,
            ),
            date_timezone=_string(
                jekyll_raw.get("date_timezone"),
                "site.jekyll.date_timezone",
            )
            or "+0100",
            author_path_prefix=_string(
                jekyll_raw.get("author_path_prefix"),
                "site.jekyll.author_path_prefix",
            )
            or "authors",
            bibtex_asset_prefix=_string(
                jekyll_raw.get("bibtex_asset_prefix"),
                "site.jekyll.bibtex_asset_prefix",
            )
            or "assets/bib",
            category_by_type=_string_mapping(
                jekyll_raw.get("category_by_type"),
                "site.jekyll.category_by_type",
            ),
            event_category_rules=_event_category_rules(
                jekyll_raw.get("event_category_rules"),
                "site.jekyll.event_category_rules",
            ),
            isbn_types=_string_tuple(
                jekyll_raw.get("isbn_types"),
                "site.jekyll.isbn_types",
                default=("book", "monograph"),
            ),
            keyword_joiner=_text(
                jekyll_raw.get("keyword_joiner"),
                "site.jekyll.keyword_joiner",
                ", ",
            ),
            tag_delimiter=_text(
                jekyll_raw.get("tag_delimiter"),
                "site.jekyll.tag_delimiter",
                ";",
            ),
        ),
    )

    return BibReviewConfig(
        source=source,
        schema_version=schema_version,
        environment=environment,
        project=project,
        arxiv=arxiv,
        paths=paths,
        discovery=discovery,
        refresh=refresh,
        audit=audit,
        references=references,
        relevance=relevance,
        providers=MappingProxyType(providers),
        site=site,
    )
