"""Transform canonical bibliography state into renderer-independent site data.

This module deliberately contains no Markdown, HTML, Liquid, Jekyll, filesystem,
or project-branding logic.  It is the stable boundary between BibReview's
canonical bibliographic model and one or more static-site renderers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..model import Publication
from ..pipeline.authors import (
    AuthorMappingError,
    author_name,
    validate_author_mappings,
)
from ..text import safe_component


class SiteTransformError(ValueError):
    """Raised when canonical state cannot be represented safely as site data."""


@dataclass(frozen=True)
class SitePublicationAuthor:
    """One source-visible author name linked to a canonical author slug."""

    slug: str
    name: str


@dataclass(frozen=True)
class SiteAuthor:
    """Canonical site-facing author identity."""

    slug: str
    name: str
    variants: tuple[str, ...]


@dataclass(frozen=True)
class SiteReference:
    """Reference enriched with an optional internal publication permalink."""

    doi: str | None
    citation: str
    permalink: str | None = None


@dataclass(frozen=True)
class SitePublication:
    """Renderer-independent publication data required by a static site."""

    id: str
    permalink: str
    created_date: date
    year: str
    type: str
    title: str
    authors: tuple[SitePublicationAuthor, ...]
    abstract: str
    container_title: str
    volume: str
    issue: str
    pages: str
    publisher: str
    event: str
    keywords: tuple[str, ...]
    identifiers: Mapping[str, str] = field(default_factory=dict)
    references: tuple[SiteReference, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "authors", tuple(self.authors))
        object.__setattr__(self, "keywords", tuple(self.keywords))
        object.__setattr__(self, "references", tuple(self.references))
        object.__setattr__(self, "identifiers", MappingProxyType(dict(self.identifiers)))


@dataclass(frozen=True)
class SiteAuthorPage:
    """One author index entry with the publications that reference that identity."""

    author: SiteAuthor
    publication_ids: tuple[str, ...]


@dataclass(frozen=True)
class SiteYearPage:
    """One publication-year index entry."""

    year: str
    publication_ids: tuple[str, ...]


@dataclass(frozen=True)
class SiteModel:
    """Complete renderer-independent site model."""

    publications: tuple[SitePublication, ...]
    authors: Mapping[str, SiteAuthorPage]
    years: Mapping[str, SiteYearPage]

    def __post_init__(self) -> None:
        object.__setattr__(self, "publications", tuple(self.publications))
        object.__setattr__(self, "authors", MappingProxyType(dict(self.authors)))
        object.__setattr__(self, "years", MappingProxyType(dict(self.years)))


def _publication_sort_key(publication: SitePublication) -> tuple[date, str, tuple[str, ...], str, str]:
    """Return a deterministic newest-first index key independent of rendering."""
    return (
        publication.created_date,
        publication.year,
        tuple(author.name for author in publication.authors),
        publication.title,
        publication.permalink,
    )


def build_site_model(
    publications: Iterable[Publication],
    author_mappings: Mapping[str, object],
) -> SiteModel:
    """Build validated static-site data without rendering or mutating project state.

    Publication order in ``SiteModel.publications`` follows the canonical input
    order.  Author/year index membership is sorted newest-first using intrinsic
    publication data, leaving presentation details to the renderer.
    """
    source = tuple(publications)
    if any(not isinstance(publication, Publication) for publication in source):
        raise SiteTransformError("publications must contain Publication objects")

    try:
        canonical_authors, reverse_authors = validate_author_mappings(author_mappings)
    except AuthorMappingError as error:
        raise SiteTransformError(str(error)) from error

    permalink_owner: dict[str, str] = {}
    doi_permalinks: dict[str, str] = {}
    for publication in source:
        try:
            permalink = safe_component(publication.permalink)
        except ValueError as error:
            raise SiteTransformError(
                f"{publication.doi or publication.id}: {error}"
            ) from error
        previous = permalink_owner.get(permalink)
        if previous is not None:
            raise SiteTransformError(
                f"duplicate publication permalink {permalink!r}: {previous} and {publication.id}"
            )
        permalink_owner[permalink] = publication.id
        if publication.doi is not None:
            previous_permalink = doi_permalinks.get(publication.doi)
            if previous_permalink is not None:
                raise SiteTransformError(f"duplicate publication DOI {publication.doi!r}")
            doi_permalinks[publication.doi] = permalink

    site_publications: list[SitePublication] = []
    author_membership: dict[str, list[SitePublication]] = {}
    year_membership: dict[str, list[SitePublication]] = {}

    for publication in source:
        if publication.created_date is None:
            raise SiteTransformError(
                f"{publication.doi or publication.id}: site rendering requires created_date"
            )
        year = publication.publication_year.strip()
        if not year or not year.isdecimal():
            raise SiteTransformError(
                f"{publication.doi or publication.id}: invalid publication year {publication.publication_year!r}"
            )

        site_authors: list[SitePublicationAuthor] = []
        for author in publication.authors:
            try:
                name = author_name(author)
            except AuthorMappingError as error:
                raise SiteTransformError(
                    f"{publication.doi or publication.id}: {error}"
                ) from error
            slug = reverse_authors.get(name)
            if slug is None:
                raise SiteTransformError(
                    f"{publication.doi or publication.id}: unmapped author {name!r}"
                )
            site_authors.append(SitePublicationAuthor(slug=slug, name=name))

        references = tuple(
            SiteReference(
                doi=reference.identifiers.get("doi"),
                citation=reference.citation,
                permalink=doi_permalinks.get(reference.identifiers.get("doi", "")),
            )
            for reference in publication.references
        )
        transformed = SitePublication(
            id=publication.id,
            permalink=publication.permalink,
            created_date=publication.created_date,
            year=year,
            type=publication.type,
            title=publication.title,
            authors=tuple(site_authors),
            abstract=publication.abstract,
            container_title=publication.container_title,
            volume=publication.volume,
            issue=publication.issue,
            pages=publication.pages,
            publisher=publication.publisher,
            event=publication.event,
            keywords=publication.keywords,
            identifiers=publication.identifiers,
            references=references,
        )
        site_publications.append(transformed)
        year_membership.setdefault(year, []).append(transformed)
        for item in site_authors:
            author_membership.setdefault(item.slug, []).append(transformed)

    author_pages: dict[str, SiteAuthorPage] = {}
    for slug in sorted(author_membership):
        variants = canonical_authors[slug]
        author = SiteAuthor(slug=slug, name=variants[0], variants=variants)
        ordered = sorted(author_membership[slug], key=_publication_sort_key, reverse=True)
        author_pages[slug] = SiteAuthorPage(
            author=author,
            publication_ids=tuple(publication.id for publication in ordered),
        )

    year_pages: dict[str, SiteYearPage] = {}
    for year in sorted(year_membership, key=int):
        ordered = sorted(year_membership[year], key=_publication_sort_key, reverse=True)
        year_pages[year] = SiteYearPage(
            year=year,
            publication_ids=tuple(publication.id for publication in ordered),
        )

    return SiteModel(
        publications=tuple(site_publications),
        authors=author_pages,
        years=year_pages,
    )


def site_model_data(model: SiteModel) -> dict[str, Any]:
    """Return a deterministic JSON-serializable representation for tests/renderers."""
    if not isinstance(model, SiteModel):
        raise SiteTransformError("model must be a SiteModel")
    return {
        "publications": [
            {
                "id": publication.id,
                "permalink": publication.permalink,
                "created_date": publication.created_date.isoformat(),
                "year": publication.year,
                "type": publication.type,
                "title": publication.title,
                "authors": [
                    {"slug": author.slug, "name": author.name}
                    for author in publication.authors
                ],
                "abstract": publication.abstract,
                "container_title": publication.container_title,
                "volume": publication.volume,
                "issue": publication.issue,
                "pages": publication.pages,
                "publisher": publication.publisher,
                "event": publication.event,
                "keywords": list(publication.keywords),
                "identifiers": dict(publication.identifiers),
                "references": [
                    {
                        "doi": reference.doi,
                        "citation": reference.citation,
                        "permalink": reference.permalink,
                    }
                    for reference in publication.references
                ],
            }
            for publication in model.publications
        ],
        "authors": {
            slug: {
                "slug": page.author.slug,
                "name": page.author.name,
                "variants": list(page.author.variants),
                "publication_ids": list(page.publication_ids),
            }
            for slug, page in model.authors.items()
        },
        "years": {
            year: {
                "year": page.year,
                "publication_ids": list(page.publication_ids),
            }
            for year, page in model.years.items()
        },
    }
