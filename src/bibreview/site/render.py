"""Pure Jekyll rendering of author/year index pages from a SiteModel.

This module returns text artifacts only.  It performs no filesystem access and
contains no project-specific prose or branding.  Projects may inject optional
editorial HTML around the generic author index without changing renderer code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import json
import re
from types import MappingProxyType
from typing import Mapping

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


@dataclass(frozen=True)
class JekyllPublicationRenderOptions:
    """Project-supplied presentation policy for Jekyll publication posts."""

    baseurl_expression: str = "{{ site.baseurl }}"
    date_timezone: str = "+0100"
    author_path_prefix: str = "authors"
    bibtex_asset_prefix: str = "assets/bib"
    category_by_type: Mapping[str, str] = field(default_factory=dict)
    event_category_rules: tuple[tuple[str, str], ...] = ()
    isbn_types: tuple[str, ...] = ("book", "monograph")
    keyword_joiner: str = ", "
    tag_delimiter: str = ";"

    def __post_init__(self) -> None:
        category_by_type = dict(self.category_by_type)
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in category_by_type.items()
        ):
            raise SiteRenderError(
                "category_by_type must map non-empty strings to non-empty strings"
            )
        rules = tuple(self.event_category_rules)
        for rule in rules:
            if (
                not isinstance(rule, tuple)
                or len(rule) != 2
                or not all(isinstance(item, str) and item for item in rule)
            ):
                raise SiteRenderError(
                    "event_category_rules must contain (pattern, category) string pairs"
                )
            try:
                re.compile(rule[0])
            except re.error as error:
                raise SiteRenderError(
                    f"invalid event category regular expression {rule[0]!r}"
                ) from error
        if any(not isinstance(item, str) or not item for item in self.isbn_types):
            raise SiteRenderError("isbn_types must contain non-empty strings")
        object.__setattr__(
            self, "category_by_type", MappingProxyType(category_by_type)
        )
        object.__setattr__(self, "event_category_rules", rules)
        object.__setattr__(self, "isbn_types", tuple(self.isbn_types))


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


def _jekyll_text(value: str, *, mathjax_backslashes: int = 1) -> str:
    """Escape Liquid delimiters and translate dollar math for a Jekyll context."""
    if mathjax_backslashes < 1:
        raise SiteRenderError("mathjax_backslashes must be positive")
    value = value.replace("{{", "{[[:space:]]{").replace("}}", "}[[:space:]]}")
    opening = "\\" * mathjax_backslashes + "( "
    closing = " " + "\\" * mathjax_backslashes + ")"
    rendered: list[str] = []
    for line in value.split("\n"):
        pieces = line.split("$")
        rendered.append(
            "".join(
                piece
                + (
                    (opening if index % 2 == 0 else closing)
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
        # The established Jekyll ordering sorts by creation date and then
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


def _publication_category(
    publication: SitePublication,
    options: JekyllPublicationRenderOptions,
) -> str:
    for pattern, category in options.event_category_rules:
        if publication.event and re.search(pattern, publication.event):
            return category
    category = options.category_by_type.get(publication.type)
    if category is None:
        identity = publication.identifiers.get("doi", publication.id)
        raise SiteRenderError(
            f"no Jekyll category configured for publication type "
            f"{publication.type!r} ({identity})"
        )
    return category


def _publication_keyword_text(
    publication: SitePublication,
    options: JekyllPublicationRenderOptions,
) -> str:
    return _jekyll_text(
        options.keyword_joiner.join(publication.keywords),
        mathjax_backslashes=2,
    )


def _render_publication_reference(reference) -> str | None:
    citation = reference.citation
    if reference.doi is None:
        return f"- {citation}" if citation else None
    if reference.permalink:
        citation = f"[{citation}]({reference.permalink})"
    return f"- {citation} -- [{reference.doi}](https://doi.org/{reference.doi})"


def _render_jekyll_publication_post(
    publication: SitePublication,
    bibtex: str,
    options: JekyllPublicationRenderOptions,
) -> RenderedArtifact:
    if not isinstance(bibtex, str):
        raise SiteRenderError(
            f"BibTeX for publication {publication.id!r} must be a string"
        )

    title = _jekyll_text(publication.title, mathjax_backslashes=2)
    names = [author.name for author in publication.authors]
    keyword_text = _publication_keyword_text(publication, options)
    category = _publication_category(publication, options)
    date_text = publication.created_date.isoformat()

    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"date: {date_text} 00:00:00 {options.date_timezone}",
        f"permalink: {publication.permalink}",
        f"year: {publication.year}",
        f"authors: {_yaml_scalar(', '.join(names))}",
        f"category: {category}",
    ]
    if keyword_text:
        lines.append("tags:")
        lines.extend(
            "  - " + _yaml_scalar(item.strip(" "))
            for item in keyword_text.split(options.tag_delimiter)
        )

    author_links = ", ".join(
        f"[{author.name}]({options.author_path_prefix}/{author.slug})"
        for author in publication.authors
    )
    lines.extend(
        [
            "---",
            " ",
            "## Authors",
            author_links,
            " ",
            "## Abstract",
            _jekyll_text(publication.abstract, mathjax_backslashes=2),
            " ",
        ]
    )
    if keyword_text:
        lines.extend(["## Keywords", keyword_text, " "])

    lines.append("## Citation")
    if publication.type in options.isbn_types:
        lines.append(f"- **ISBN:** {publication.identifiers.get('isbn', '')}")
    else:
        for label, value in (
            ("Journal", publication.container_title),
            ("Year", publication.year),
            ("Volume", publication.volume),
            ("Issue", publication.issue),
            ("Pages", publication.pages),
        ):
            lines.append(f"- **{label}:** {value}")

    lines.append(f"- **Publisher:** {publication.publisher}")
    doi = publication.identifiers.get("doi")
    if doi is not None:
        lines.append(f"- **DOI:** [{doi}](https://doi.org/{doi})")
    if publication.event:
        lines.append(f"- **Note:** {publication.event}")

    lines.extend([" ", "## BibTeX", "{% highlight bibtex %}", "{% raw %}"])
    rendered = "\n".join(lines) + "\n" + bibtex
    rendered += "\n".join(
        [
            "{% endraw %}",
            "{% endhighlight %}",
            " ",
            f"[Download the bib file]({options.baseurl_expression}/"
            f"{options.bibtex_asset_prefix}/{publication.permalink}.bib)",
            " ",
            "",
        ]
    )

    references = [
        item
        for item in (
            _render_publication_reference(reference)
            for reference in publication.references
        )
        if item is not None
    ]
    if references:
        rendered += "## References\n" + "\n".join(references) + "\n\n"

    return RenderedArtifact(
        path=f"_posts/{date_text}-{publication.permalink}.md",
        content=rendered,
    )


def render_jekyll_publication_posts(
    model: SiteModel,
    bibtex_by_publication_id: Mapping[str, str],
    *,
    options: JekyllPublicationRenderOptions | None = None,
) -> tuple[RenderedArtifact, ...]:
    """Render Jekyll publication posts without network or filesystem access."""
    if not isinstance(model, SiteModel):
        raise SiteRenderError("model must be a SiteModel")
    if not isinstance(bibtex_by_publication_id, Mapping):
        raise SiteRenderError("bibtex_by_publication_id must be a mapping")
    options = options or JekyllPublicationRenderOptions()

    artifacts: list[RenderedArtifact] = []
    paths: set[str] = set()
    for publication in model.publications:
        if publication.id not in bibtex_by_publication_id:
            raise SiteRenderError(
                f"missing BibTeX for publication id {publication.id!r}"
            )
        artifact = _render_jekyll_publication_post(
            publication,
            bibtex_by_publication_id[publication.id],
            options,
        )
        if artifact.path in paths:
            raise SiteRenderError(f"duplicate rendered artifact path {artifact.path!r}")
        paths.add(artifact.path)
        artifacts.append(artifact)
    return tuple(artifacts)


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
