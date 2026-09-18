"""Project-level static-site rendering and persistence orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .config import BibReviewConfig
from .site import (
    JekyllIndexRenderOptions,
    JekyllPublicationRenderOptions,
    RenderedArtifact,
    SitePersistencePlan,
    apply_rendered_artifacts,
    build_site_model,
    plan_rendered_artifacts,
    render_jekyll_index_pages,
    render_jekyll_publication_posts,
)
from .storage import (
    bibliography_metadata_data,
    read_bibliography_document,
    read_json,
)


class ProjectRenderError(ValueError):
    """Raised when a configured project cannot be rendered safely."""


@dataclass(frozen=True)
class ProjectRenderPlan:
    """Read-only plan for one complete configured site-rendering pass."""

    artifacts: tuple[RenderedArtifact, ...]
    persistence: SitePersistencePlan
    orphan_bibtex: tuple[Path, ...] = ()

    @property
    def changed(self) -> bool:
        return self.persistence.changed

    def summary(self) -> str:
        return (
            f"{self.persistence.summary()}; "
            f"orphan BibTeX: {len(self.orphan_bibtex)}"
        )


def _jekyll_index_options(config: BibReviewConfig) -> JekyllIndexRenderOptions:
    policy = config.site.jekyll
    return JekyllIndexRenderOptions(
        baseurl_expression=policy.baseurl_expression,
        count_posts_include=policy.count_posts_include,
        author_index_extra_html=policy.author_index_extra_html,
        include_authorless_year_publications=(
            policy.include_authorless_year_publications
        ),
    )


def _jekyll_publication_options(
    config: BibReviewConfig,
) -> JekyllPublicationRenderOptions:
    policy = config.site.jekyll
    return JekyllPublicationRenderOptions(
        baseurl_expression=policy.baseurl_expression,
        date_timezone=policy.date_timezone,
        author_path_prefix=policy.author_path_prefix,
        bibtex_asset_prefix=policy.bibtex_asset_prefix,
        category_by_type=policy.category_by_type,
        event_category_rules=policy.event_category_rules,
        isbn_types=policy.isbn_types,
        keyword_joiner=policy.keyword_joiner,
        tag_delimiter=policy.tag_delimiter,
    )


def _read_bibtex(
    config: BibReviewConfig,
    publications,
) -> tuple[dict[str, str], tuple[Path, ...]]:
    expected: set[Path] = set()
    by_id: dict[str, str] = {}
    missing: list[Path] = []

    for publication in publications:
        target = config.paths.bibtex / f"{publication.permalink}.bib"
        expected.add(target.resolve())
        if not target.is_file():
            missing.append(target)
            continue
        by_id[publication.id] = target.read_text(encoding="utf-8")

    if missing:
        relative = ", ".join(
            str(path.relative_to(config.source.parent))
            if path.is_relative_to(config.source.parent)
            else str(path)
            for path in missing
        )
        raise ProjectRenderError(
            "site rendering requires tracked BibTeX for every publication; "
            f"missing: {relative}"
        )

    orphan = tuple(
        sorted(
            (
                path
                for path in config.paths.bibtex.rglob("*.bib")
                if path.resolve() not in expected
            ),
            key=lambda path: path.as_posix(),
        )
    )
    return by_id, orphan


def plan_project_render(config: BibReviewConfig) -> ProjectRenderPlan:
    """Render the configured site in memory and return a safe persistence plan."""
    if not config.site.enabled:
        raise ProjectRenderError("site rendering is disabled by site.enabled=false")
    if config.site.implementation != "jekyll":
        raise ProjectRenderError(
            "unsupported site implementation "
            f"{config.site.implementation!r}; expected 'jekyll'"
        )
    if config.site.source is None:
        raise ProjectRenderError("site.source is required for site rendering")

    document = read_bibliography_document(config.paths.bibliography)
    author_mappings = read_json(config.paths.author_mappings, dict)
    model = build_site_model(document.publications, author_mappings)
    bibtex_by_id, orphan_bibtex = _read_bibtex(config, model.publications)

    metadata_artifact = RenderedArtifact(
        path="_data/bibreview/metadata.json",
        content=json.dumps(
            bibliography_metadata_data(document.metadata),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
    )

    artifacts = (
        metadata_artifact,
        *render_jekyll_publication_posts(
            model,
            bibtex_by_id,
            options=_jekyll_publication_options(config),
        ),
        *render_jekyll_index_pages(
            model,
            options=_jekyll_index_options(config),
        ),
    )
    artifacts = tuple(artifacts)
    persistence = plan_rendered_artifacts(
        config.site.source,
        artifacts,
        managed_roots=(
            "_posts",
            "authors",
            "years",
            "_data/bibreview",
        ),
    )
    return ProjectRenderPlan(
        artifacts=artifacts,
        persistence=persistence,
        orphan_bibtex=orphan_bibtex,
    )


def apply_project_render(plan: ProjectRenderPlan) -> None:
    """Apply a previously validated configured site-rendering plan."""
    if not isinstance(plan, ProjectRenderPlan):
        raise ProjectRenderError("plan must be a ProjectRenderPlan")
    apply_rendered_artifacts(plan.persistence)
