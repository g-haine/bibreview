"""Pure Jekyll rendering of author/year index pages from a SiteModel.

This module returns text artifacts only.  It performs no filesystem access and
contains no PHRAISE-specific prose or branding.  Projects may inject optional
editorial HTML around the generic author index without changing renderer code.
"""

from __future__ import annotations

from dataclasses import dataclass
import html
import json
import re

from unidecode import unidecode

from .transform import SiteModel, SitePublication


class SiteRenderError(ValueError):
    """Raised when validated site data cannot be rendered safely."""


@dataclass(frozen=True)
class RenderedArtifact:
    """One renderer output before any persistence layer writes it."""

    path: str
    content: str


@dataclass(frozen=True)
class JekyllIndexRenderOptions:
    """Project-supplied presentation options for generic Jekyll index pages."""

    baseurl_expression: str = "{{ site.baseurl }}"
    count_posts_include: str = "{% include count-posts.html %}"
    author_index_extra_html: str = ""
    include_authorless_year_publications: bool = True


def _yaml_scalar(value: str) -> str:
    """Render a conservative YAML scalar compatible with Jekyll front matter."""
    if (
        not value
        or re.search(r"[:#\n\r\[\]{}]", value)
        or value[0] in "!&*?|-<>=@`\"'"
        or value.lower() in {"null", "true", "false", "yes", "no", "on", "off", "~"}
    ):
        return json.dumps(value, ensure_ascii=False)
    return value


def _page_header(title: str, permalink: str) -> str:
    return (
        "---\n"
        f"title: {_yaml_scalar(title)}\n"
        f"permalink: {permalink}\n"
        "---\n\n"
    )


def _jekyll_text(value: str) -> str:
    """Escape Liquid delimiters and translate dollar math to MathJax delimiters."""
    value = value.replace("{{", "{[[:space:]]{").replace("}}", "}[[:space:]]}")
    rendered: list[str] = []
    for line in value.split("\n"):
        pieces = line.split("$")
        rendered.append(
            "".join(
                piece
                + (
                    ("\\( " if index % 2 == 0 else " \\)")
                    if index < len(pieces) - 1
                    else ""
                )
                for index, piece in enumerate(pieces)
            )
        )
    return "\n".join(rendered)


def _publication_row(publication: SitePublication, options: JekyllIndexRenderOptions) -> str:
    names = ", ".join(author.name for author in publication.authors)
    title = html.escape(_jekyll_text(publication.title), quote=False)
    metadata = html.escape(f"{publication.year} -- {names}", quote=False)
    return (
        f"<li><span class='post-meta'>{metadata}</span>"
        f"<h3><a class='post-link' href=\"{options.baseurl_expression}/{publication.permalink}\">"
        f"{title}</a></h3></li>"
    )


def _publication_list(
    publication_ids: tuple[str, ...],
    publications: dict[str, SitePublication],
    options: JekyllIndexRenderOptions,
) -> str:
    rows: list[tuple[object, str]] = []
    for publication_id in publication_ids:
        publication = publications.get(publication_id)
        if publication is None:
            raise SiteRenderError(
                f"site index references missing publication id {publication_id!r}"
            )
        row = _publication_row(publication, options)
        # PHRAISE's historical Jekyll renderer sorted by creation date and then
        # by the complete rendered row.  Keep that presentation-specific
        # tie-break here rather than leaking HTML ordering into SiteModel.
        rows.append((publication.created_date, row))
    rows.sort(reverse=True)
    result = '<ul class="post-list">\n'
    result += "\n".join(row for _, row in rows)
    return result + f"\n\n</ul>\n{options.count_posts_include}\n"


def _author_sort_key(name: str) -> tuple[str, str]:
    words = name.split()
    if not words:
        raise SiteRenderError("author display name cannot be empty")
    surname = unidecode(words[-1]).replace("d'", "")
    return surname, name


def _author_letter(name: str) -> str:
    surname, _ = _author_sort_key(name)
    if not surname:
        raise SiteRenderError(f"cannot determine index letter for author {name!r}")
    return surname[0].upper()


def render_jekyll_index_pages(
    model: SiteModel,
    *,
    options: JekyllIndexRenderOptions | None = None,
) -> tuple[RenderedArtifact, ...]:
    """Render generic Jekyll author/year pages without touching the filesystem."""
    if not isinstance(model, SiteModel):
        raise SiteRenderError("model must be a SiteModel")
    options = options or JekyllIndexRenderOptions()
    publications = {publication.id: publication for publication in model.publications}
    if len(publications) != len(model.publications):
        raise SiteRenderError("duplicate publication id in SiteModel")

    artifacts: dict[str, str] = {}

    author_index = _page_header("Authors", "/authors/")
    author_index += f"<h3>There are {len(model.authors)} authors referenced.</h3>\n"
    if options.author_index_extra_html:
        author_index += options.author_index_extra_html
        if not options.author_index_extra_html.endswith("\n"):
            author_index += "\n"
    author_index += "<p id='links-letters'>" + " - ".join(
        f"<a href='#{letter}'>{letter.upper()}</a>"
        for letter in "abcdefghijklmnopqrstuvwxyz"
    ) + "</p>\n"
    author_index += "<div class='grid'>\n"

    current_letter = ""
    ordered_authors = sorted(
        model.authors.items(),
        key=lambda item: _author_sort_key(item[1].author.name),
    )
    for slug, page in ordered_authors:
        if slug == "index":
            raise SiteRenderError("author slug 'index' conflicts with authors/index.md")
        author = page.author.name
        letter = _author_letter(author)
        if letter != current_letter:
            author_index += f"</div>\n## {letter}\n<div class='grid'>\n"
            current_letter = letter
        author_index += (
            f"<a href='{options.baseurl_expression}/authors/{slug}'>"
            f"{html.escape(author)}</a>\n"
        )

        content = _page_header(f"Publications by {author}", f"/authors/{slug}")
        content += '<h3 id="number-posts">There are ... items referenced.</h3>\n'
        variants = html.escape(", ".join(page.author.variants))
        content += (
            "<p id='info-authors'>Alternative author names: "
            f"{variants}.</p>\n<hr />\n"
        )
        content += _publication_list(page.publication_ids, publications, options)
        artifacts[f"authors/{slug}.md"] = content

    artifacts["authors/index.md"] = author_index + "</div>\n"

    year_index = _page_header("Years", "/years/") + '<div class="grid">\n'
    for year, page in model.years.items():
        if not year.isdecimal():
            raise SiteRenderError(f"invalid site year {year!r}")
        publication_ids = page.publication_ids
        if not options.include_authorless_year_publications:
            filtered: list[str] = []
            for publication_id in publication_ids:
                publication = publications.get(publication_id)
                if publication is None:
                    raise SiteRenderError(
                        f"site index references missing publication id {publication_id!r}"
                    )
                if publication.authors:
                    filtered.append(publication_id)
            publication_ids = tuple(filtered)
        if not publication_ids:
            continue
        year_index += (
            f"<a href='{options.baseurl_expression}/years/{year}'>{year}</a>\n"
        )
        content = _page_header(f"Published in {year}", f"/years/{year}")
        content += '<h3 id="number-posts">There are ... items referenced.</h3>\n'
        content += _publication_list(publication_ids, publications, options)
        artifacts[f"years/{year}.md"] = content
    artifacts["years/index.md"] = year_index + "</div>\n"

    return tuple(
        RenderedArtifact(path=path, content=artifacts[path])
        for path in sorted(artifacts)
    )
