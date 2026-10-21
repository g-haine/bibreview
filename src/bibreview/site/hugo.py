"""Pure Hugo rendering from renderer-independent SiteModel data.

The renderer emits Markdown content and leaves theme/layout policy to the Hugo
site.  All links between generated pages are relative so projects deployed
under a subpath (for example GitHub Pages project sites) do not require the
renderer to know Hugo's final baseURL.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
from typing import Mapping

from .render import RenderedArtifact, SiteRenderError
from .transform import SiteModel, SitePublication


@dataclass(frozen=True)
class HugoRenderOptions:
    """Project-supplied path policy for Hugo-generated content."""

    publication_path_prefix: str = "publications"
    author_path_prefix: str = "authors"
    year_path_prefix: str = "years"
    bibtex_asset_prefix: str = "bib"
    include_authorless_year_publications: bool = True

    def __post_init__(self) -> None:
        for name in (
            "publication_path_prefix",
            "author_path_prefix",
            "year_path_prefix",
            "bibtex_asset_prefix",
        ):
            value = getattr(self, name)
            path = PurePosixPath(value)
            if (
                not isinstance(value, str)
                or not value
                or path.is_absolute()
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise SiteRenderError(
                    f"{name} must be a non-empty safe relative path"
                )


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _yaml_list_lines(
    key: str,
    values: tuple[str, ...] | list[str],
) -> list[str]:
    if not values:
        return [f"{key}: []"]
    return [f"{key}:", *(f"  - {_yaml_string(value)}" for value in values)]


def _markdown_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _hugo_text(value: str) -> str:
    """Prevent canonical text from being interpreted as a Hugo shortcode."""
    return value.replace("{{<", "&#123;&#123;&lt;").replace("{{%", "&#123;&#123;%")


def _render_lines(lines: list[str]) -> str:
    """Render Markdown lines with exactly one trailing newline."""
    return "\n".join(lines).rstrip("\n") + "\n"


def _metadata_line(label: str, value: str) -> str:
    """Render a Markdown metadata item without trailing whitespace."""
    rendered = _hugo_text(value)
    suffix = f" {rendered}" if rendered else ""
    return f"- **{label}:**{suffix}"


def _publication_names(publication: SitePublication) -> str:
    if publication.authors:
        return ", ".join(author.name for author in publication.authors)
    label = "Ed." if len(publication.editors) == 1 else "Eds."
    return f"{label} " + ", ".join(editor.name for editor in publication.editors)


def _publication_link(
    publication: SitePublication,
    *,
    from_section: bool = True,
    publication_path_prefix: str,
) -> str:
    if from_section:
        return f"../../{publication_path_prefix}/{publication.permalink}/"
    return f"./{publication.permalink}/"


def _render_reference(
    reference,
    *,
    publication_path_prefix: str,
) -> str | None:
    citation = _hugo_text(reference.citation)
    doi_link = (
        f"[{reference.doi}](https://doi.org/{reference.doi})"
        if reference.doi
        else ""
    )
    if reference.permalink and citation:
        citation = (
            f"[{_markdown_label(citation)}]"
            f"(../{reference.permalink}/)"
        )
    if citation and doi_link:
        return f"- {citation} -- {doi_link}"
    if citation:
        return f"- {citation}"
    if doi_link:
        return f"- {doi_link}"
    return None


def _render_publication(
    publication: SitePublication,
    bibtex: str,
    options: HugoRenderOptions,
) -> RenderedArtifact:
    names = tuple(author.name for author in publication.authors)
    slugs = tuple(author.slug for author in publication.authors)
    editors = tuple(editor.name for editor in publication.editors)

    lines = [
        "---",
        f"title: {_yaml_string(publication.title)}",
        f"date: {publication.created_date.isoformat()}",
        f"year: {_yaml_string(publication.year)}",
        f"publication_type: {_yaml_string(publication.type)}",
        *_yaml_list_lines("authors", names),
        *_yaml_list_lines("author_slugs", slugs),
    ]
    if editors:
        lines.extend(_yaml_list_lines("editors", editors))
    if publication.keywords:
        lines.extend(_yaml_list_lines("tags", list(publication.keywords)))
    lines.extend(["---", ""])

    if publication.authors:
        author_links = ", ".join(
            f"[{_markdown_label(author.name)}]"
            f"(../../{options.author_path_prefix}/{author.slug}/)"
            for author in publication.authors
        )
        lines.extend(["## Authors", author_links, ""])
    if publication.editors:
        lines.extend(
            [
                "## Editors",
                ", ".join(_markdown_label(name) for name in editors),
                "",
            ]
        )

    lines.extend(["## Abstract", _hugo_text(publication.abstract), ""])

    if publication.keywords:
        lines.extend(
            [
                "## Keywords",
                ", ".join(_hugo_text(item) for item in publication.keywords),
                "",
            ]
        )

    lines.append("## Citation")
    if publication.type in {"book", "monograph"}:
        lines.append(_metadata_line("ISBN", publication.identifiers.get("isbn", "")))
    else:
        for label, value in (
            ("Journal", publication.container_title),
            ("Year", publication.year),
            ("Volume", publication.volume),
            ("Issue", publication.issue),
            ("Pages", publication.pages),
        ):
            lines.append(_metadata_line(label, str(value)))
    lines.append(_metadata_line("Publisher", publication.publisher))
    doi = publication.identifiers.get("doi")
    if doi:
        lines.append(f"- **DOI:** [{doi}](https://doi.org/{doi})")
    if publication.event:
        lines.append(f"- **Note:** {_hugo_text(publication.event)}")

    lines.extend(
        [
            "",
            "## BibTeX",
            "~~~bibtex",
            bibtex.rstrip("\n"),
            "~~~",
            "",
            (
                "[Download the bib file]"
                f"(../../{options.bibtex_asset_prefix}/{publication.permalink}.bib)"
            ),
            "",
        ]
    )

    references = [
        rendered
        for rendered in (
            _render_reference(
                reference,
                publication_path_prefix=options.publication_path_prefix,
            )
            for reference in publication.references
        )
        if rendered is not None
    ]
    if references:
        lines.extend(["## References", *references, ""])

    return RenderedArtifact(
        path=(
            f"content/{options.publication_path_prefix}/"
            f"{publication.permalink}.md"
        ),
        content=_render_lines(lines),
    )


def _publication_row(
    publication: SitePublication,
    *,
    options: HugoRenderOptions,
) -> str:
    label = _markdown_label(publication.title)
    names = _markdown_label(_publication_names(publication))
    return (
        f"- **{publication.year} — {names}.** "
        f"[{label}]"
        f"({_publication_link(publication, publication_path_prefix=options.publication_path_prefix)})"
    )


def _render_author_pages(
    model: SiteModel,
    options: HugoRenderOptions,
) -> tuple[RenderedArtifact, ...]:
    publications = {publication.id: publication for publication in model.publications}
    artifacts: list[RenderedArtifact] = []

    index_lines = ["---", 'title: "Authors"', "---", ""]
    authors = sorted(
        model.authors.values(),
        key=lambda page: (page.author.name.casefold(), page.author.slug),
    )
    for page in authors:
        index_lines.append(
            f"- [{_markdown_label(page.author.name)}](./{page.author.slug}/)"
        )
        body = [
            "---",
            f"title: {_yaml_string(page.author.name)}",
            "---",
            "",
        ]
        for publication_id in page.publication_ids:
            publication = publications.get(publication_id)
            if publication is None:
                raise SiteRenderError(
                    f"Hugo author page references missing publication id "
                    f"{publication_id!r}"
                )
            body.append(_publication_row(publication, options=options))
        body.append("")
        artifacts.append(
            RenderedArtifact(
                path=f"content/{options.author_path_prefix}/{page.author.slug}.md",
                content=_render_lines(body),
            )
        )
    index_lines.append("")
    artifacts.append(
        RenderedArtifact(
            path=f"content/{options.author_path_prefix}/_index.md",
            content=_render_lines(index_lines),
        )
    )
    return tuple(artifacts)


def _render_year_pages(
    model: SiteModel,
    options: HugoRenderOptions,
) -> tuple[RenderedArtifact, ...]:
    publications = {publication.id: publication for publication in model.publications}
    artifacts: list[RenderedArtifact] = []
    index_lines = ["---", 'title: "Years"', "---", ""]

    for year, page in sorted(model.years.items(), reverse=True):
        selected: list[SitePublication] = []
        for publication_id in page.publication_ids:
            publication = publications.get(publication_id)
            if publication is None:
                raise SiteRenderError(
                    f"Hugo year page references missing publication id "
                    f"{publication_id!r}"
                )
            if (
                not options.include_authorless_year_publications
                and not publication.authors
            ):
                continue
            selected.append(publication)

        if not selected:
            continue

        index_lines.append(f"- [{year}](./{year}/) ({len(selected)})")
        body = ["---", f"title: {_yaml_string(year)}", "---", ""]
        body.extend(_publication_row(publication, options=options) for publication in selected)
        body.append("")
        artifacts.append(
            RenderedArtifact(
                path=f"content/{options.year_path_prefix}/{year}.md",
                content=_render_lines(body),
            )
        )

    index_lines.append("")
    artifacts.append(
        RenderedArtifact(
            path=f"content/{options.year_path_prefix}/_index.md",
            content=_render_lines(index_lines),
        )
    )
    return tuple(artifacts)


def render_hugo_site(
    model: SiteModel,
    bibtex_by_publication_id: Mapping[str, str],
    *,
    options: HugoRenderOptions | None = None,
) -> tuple[RenderedArtifact, ...]:
    """Render publication, author, and year Markdown for a Hugo content tree."""
    if not isinstance(model, SiteModel):
        raise SiteRenderError("model must be a SiteModel")
    options = options or HugoRenderOptions()

    publications: list[RenderedArtifact] = []
    for publication in model.publications:
        bibtex = bibtex_by_publication_id.get(publication.id)
        if bibtex is None:
            raise SiteRenderError(
                f"missing BibTeX for publication {publication.id!r}"
            )
        if not isinstance(bibtex, str):
            raise SiteRenderError(
                f"BibTeX for publication {publication.id!r} must be a string"
            )
        publications.append(_render_publication(publication, bibtex, options))

    return (
        *publications,
        *_render_author_pages(model, options),
        *_render_year_pages(model, options),
    )
