from __future__ import annotations

from datetime import date
import unittest

from bibreview.site import (
    JekyllPublicationRenderOptions,
    SiteModel,
    SitePublication,
    SitePublicationAuthor,
    SiteReference,
    SiteRenderError,
    render_jekyll_publication_posts,
)


def publication(
    *,
    publication_id: str = "pub-id",
    publication_type: str = "journal-article",
    doi: str | None = "10.1234/example",
    event: str = "",
    authors: tuple[SitePublicationAuthor, ...] | None = None,
    references: tuple[SiteReference, ...] = (),
) -> SitePublication:
    identifiers = {}
    if doi is not None:
        identifiers["doi"] = doi
    if publication_type in {"book", "monograph"}:
        identifiers["isbn"] = "978-1-2345-6789-0"
    return SitePublication(
        id=publication_id,
        permalink="example-publication",
        created_date=date(2025, 5, 10),
        year="2025",
        type=publication_type,
        title="A $x$ title",
        authors=(
            authors
            if authors is not None
            else (
                SitePublicationAuthor(
                    slug="ada-lovelace",
                    name="Ada Lovelace",
                ),
            )
        ),
        abstract="An $H$ abstract.",
        container_title="Journal of Examples",
        volume="12",
        issue="3",
        pages="10--20",
        publisher="Example Publisher",
        event=event,
        keywords=("port-Hamiltonian", "energy"),
        identifiers=identifiers,
        references=references,
    )


def model(item: SitePublication) -> SiteModel:
    return SiteModel(publications=(item,), authors={}, years={})


def options() -> JekyllPublicationRenderOptions:
    return JekyllPublicationRenderOptions(
        category_by_type={
            "journal-article": "articles",
            "proceedings-article": "proceedings",
            "book-chapter": "chapters",
            "book": "books",
            "monograph": "books",
        },
        event_category_rules=(
            (
                r"Conference|Workshop|Symposium|Congress|Proceeding|Consortium",
                "proceedings",
            ),
        ),
    )


class JekyllPublicationRendererTests(unittest.TestCase):
    def test_renders_journal_post_exactly(self) -> None:
        item = publication(
            references=(
                SiteReference(
                    doi="10.1234/internal",
                    citation="Internal reference",
                    permalink="internal-work",
                ),
                SiteReference(
                    doi="10.1234/external",
                    citation="External reference",
                ),
                SiteReference(
                    doi=None,
                    citation="Reference without DOI",
                ),
            )
        )
        bibtex = "@article{Example,\n  title={Example}\n}\n"
        rendered = render_jekyll_publication_posts(
            model(item),
            {"pub-id": bibtex},
            options=options(),
        )
        self.assertEqual(len(rendered), 1)
        self.assertEqual(
            rendered[0].path,
            "_posts/2025-05-10-example-publication.md",
        )
        self.assertEqual(
            rendered[0].content,
            """---
title: "A \\\\( x \\\\) title"
date: 2025-05-10 00:00:00 +0100
permalink: example-publication
year: 2025
authors: Ada Lovelace
category: articles
tags:
  - port-Hamiltonian, energy
---
 
## Authors
[Ada Lovelace](authors/ada-lovelace)
 
## Abstract
An \\( H \\) abstract.
 
## Keywords
port-Hamiltonian, energy
 
## Citation
- **Journal:** Journal of Examples
- **Year:** 2025
- **Volume:** 12
- **Issue:** 3
- **Pages:** 10--20
- **Publisher:** Example Publisher
- **DOI:** [10.1234/example](https://doi.org/10.1234/example)
 
## BibTeX
{% highlight bibtex %}
{% raw %}
@article{Example,
  title={Example}
}
{% endraw %}
{% endhighlight %}
 
[Download the bib file]({{ site.baseurl }}/assets/bib/example-publication.bib)
 
## References
- [Internal reference](internal-work) -- [10.1234/internal](https://doi.org/10.1234/internal)
- External reference -- [10.1234/external](https://doi.org/10.1234/external)
- Reference without DOI

""",
        )

    def test_event_rule_can_override_type_category(self) -> None:
        item = publication(event="Presented at Example Conference 2025")
        content = render_jekyll_publication_posts(
            model(item),
            {"pub-id": "@article{x}\n"},
            options=options(),
        )[0].content
        self.assertIn("category: proceedings", content)
        self.assertIn("- **Note:** Presented at Example Conference 2025", content)

    def test_book_uses_isbn_instead_of_journal_fields(self) -> None:
        item = publication(publication_type="book")
        content = render_jekyll_publication_posts(
            model(item),
            {"pub-id": "@book{x}\n"},
            options=options(),
        )[0].content
        self.assertIn("- **ISBN:** 978-1-2345-6789-0", content)
        self.assertNotIn("- **Journal:**", content)

    def test_no_doi_is_supported(self) -> None:
        item = publication(doi=None)
        content = render_jekyll_publication_posts(
            model(item),
            {"pub-id": "@misc{x}\n"},
            options=options(),
        )[0].content
        self.assertNotIn("**DOI:**", content)

    def test_authorless_publication_is_supported(self) -> None:
        item = publication(authors=())
        content = render_jekyll_publication_posts(
            model(item),
            {"pub-id": "@misc{x}\n"},
            options=options(),
        )[0].content
        self.assertIn('authors: ""', content)
        self.assertIn("## Authors\n\n ", content)

    def test_missing_bibtex_is_rejected(self) -> None:
        with self.assertRaisesRegex(SiteRenderError, "missing BibTeX"):
            render_jekyll_publication_posts(
                model(publication()),
                {},
                options=options(),
            )

    def test_missing_category_is_rejected(self) -> None:
        with self.assertRaisesRegex(SiteRenderError, "no Jekyll category configured"):
            render_jekyll_publication_posts(
                model(publication()),
                {"pub-id": "@article{x}\n"},
            )

    def test_invalid_event_pattern_is_rejected(self) -> None:
        with self.assertRaisesRegex(SiteRenderError, "invalid event category"):
            JekyllPublicationRenderOptions(
                category_by_type={"journal-article": "articles"},
                event_category_rules=(("[", "proceedings"),),
            )


if __name__ == "__main__":
    unittest.main()
